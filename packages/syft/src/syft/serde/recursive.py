# stdlib
from enum import Enum
import sys
import threading
import types
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Set
from typing import Type
from typing import Union

# third party
from capnp.lib.capnp import _DynamicStructBuilder
from pydantic import BaseModel

# syft absolute
import syft as sy

# relative
from ..util.util import get_fully_qualified_name
from ..util.util import index_syft_by_module_name
from .capnp import get_capnp_schema

TYPE_BANK = {}

recursive_scheme = get_capnp_schema("recursive_serde.capnp").RecursiveSerde  # type: ignore


def thread_ident() -> int:
    return int(threading.current_thread().ident)


def recursive_serde_register(
    cls: Union[object, type],
    serialize: Optional[Callable] = None,
    deserialize: Optional[Callable] = None,
    serialize_attrs: Optional[List] = None,
    exclude_attrs: Optional[List] = None,
    inherit_attrs: Optional[bool] = True,
    inheritable_attrs: Optional[bool] = True,
) -> None:
    pydantic_fields = None
    base_attrs = None
    attribute_list: Set[str] = set()

    cls = type(cls) if not isinstance(cls, type) else cls
    fqn = f"{cls.__module__}.{cls.__name__}"

    nonrecursive = bool(serialize and deserialize)
    _serialize = serialize if nonrecursive else rs_object2proto
    _deserialize = deserialize if nonrecursive else rs_proto2object
    is_pydantic = issubclass(cls, BaseModel)

    if inherit_attrs and not is_pydantic:
        # get attrs from base class
        base_attrs = getattr(cls, "__syft_serializable__", [])
        attribute_list.update(base_attrs)

    if is_pydantic and not serialize_attrs:
        # if pydantic object and attrs are provided, the get attrs from __fields__
        # cls.__fields__ auto inherits attrs
        pydantic_fields = [
            f.name
            for f in cls.__fields__.values()
            if f.outer_type_ not in (Callable, types.FunctionType, types.LambdaType)
        ]
        attribute_list.update(pydantic_fields)

    if serialize_attrs:
        # If serialize_attrs is provided, append it to our attr list
        attribute_list.update(serialize_attrs)

    if exclude_attrs:
        attribute_list = attribute_list - set(exclude_attrs)

    if issubclass(cls, Enum):
        attribute_list.update(["value"])

    if inheritable_attrs and attribute_list and not is_pydantic:
        # only set __syft_serializable__ for non-pydantic classes because
        # pydantic objects inherit by default
        setattr(cls, "__syft_serializable__", attribute_list)

    attributes = set(list(attribute_list)) if attribute_list else None
    serde_overrides = getattr(cls, "__serde_overrides__", {})

    # without fqn duplicate class names overwrite
    TYPE_BANK[fqn] = (
        nonrecursive,
        _serialize,
        _deserialize,
        attributes,
        serde_overrides,
        cls,
    )


def chunk_bytes(
    data: bytes, field_name: Union[str, int], builder: _DynamicStructBuilder
) -> None:
    CHUNK_SIZE = int(5.12e8)  # capnp max for a List(Data) field
    list_size = len(data) // CHUNK_SIZE + 1
    data_lst = builder.init(field_name, list_size)
    END_INDEX = CHUNK_SIZE
    for idx in range(list_size):
        START_INDEX = idx * CHUNK_SIZE
        END_INDEX = min(START_INDEX + CHUNK_SIZE, len(data))
        data_lst[idx] = data[START_INDEX:END_INDEX]


def combine_bytes(capnp_list: List[bytes]) -> bytes:
    # TODO: make sure this doesn't copy, perhaps allocate a fixed size buffer
    # and move the bytes into it as we go
    bytes_value = b""
    for value in capnp_list:
        bytes_value += value
    return bytes_value


def rs_object2proto(self: Any) -> _DynamicStructBuilder:
    is_type = False
    if isinstance(self, type):
        is_type = True

    msg = recursive_scheme.new_message()
    fqn = get_fully_qualified_name(self)
    if fqn not in TYPE_BANK:
        # third party
        raise Exception(f"{fqn} not in TYPE_BANK")

    msg.fullyQualifiedName = fqn
    (
        nonrecursive,
        serialize,
        deserialize,
        attribute_list,
        serde_overrides,
        cls,
    ) = TYPE_BANK[fqn]

    if nonrecursive or is_type:
        if serialize is None:
            raise Exception(
                f"Cant serialize {type(self)} nonrecursive without serialize."
            )
        chunk_bytes(serialize(self), "nonrecursiveBlob", msg)
        return msg

    if attribute_list is None:
        attribute_list = self.__dict__.keys()

    msg.init("fieldsName", len(attribute_list))
    msg.init("fieldsData", len(attribute_list))

    for idx, attr_name in enumerate(sorted(attribute_list)):
        if not hasattr(self, attr_name):
            raise ValueError(
                f"{attr_name} on {type(self)} does not exist, serialization aborted!"
            )

        field_obj = getattr(self, attr_name)
        transforms = serde_overrides.get(attr_name, None)

        if transforms is not None:
            field_obj = transforms[0](field_obj)

        if isinstance(field_obj, types.FunctionType):
            continue

        serialized = sy.serialize(field_obj, to_bytes=True)
        msg.fieldsName[idx] = attr_name
        chunk_bytes(serialized, idx, msg.fieldsData)

    return msg


def rs_bytes2object(blob: bytes) -> Any:
    MAX_TRAVERSAL_LIMIT = 2**64 - 1

    with recursive_scheme.from_bytes(  # type: ignore
        blob, traversal_limit_in_words=MAX_TRAVERSAL_LIMIT
    ) as msg:
        return rs_proto2object(msg)


def rs_proto2object(proto: _DynamicStructBuilder) -> Any:
    # relative
    from .deserialize import _deserialize

    # clean this mess, Tudor
    module_parts = proto.fullyQualifiedName.split(".")
    klass = module_parts.pop()
    class_type: Type = type(None)

    if klass != "NoneType":
        try:
            class_type = index_syft_by_module_name(proto.fullyQualifiedName)  # type: ignore
        except Exception:  # nosec
            try:
                class_type = getattr(sys.modules[".".join(module_parts)], klass)
            except Exception:  # nosec
                if "syft.user" in proto.fullyQualifiedName:
                    # relative
                    from ..node.node import CODE_RELOADER

                    for _, load_user_code in CODE_RELOADER.items():
                        load_user_code()
                try:
                    class_type = getattr(sys.modules[".".join(module_parts)], klass)
                except Exception:  # nosec
                    pass

    if proto.fullyQualifiedName not in TYPE_BANK:
        raise Exception(f"{proto.fullyQualifiedName} not in TYPE_BANK")

    # TODO: 🐉 sort this out, basically sometimes the syft.user classes are not in the
    # module name space in sub-processes or threads even though they are loaded on start
    # its possible that the uvicorn awsgi server is preloading a bunch of threads
    # however simply getting the class from the TYPE_BANK doesn't always work and
    # causes some errors so it seems like we want to get the local one where possible
    (
        nonrecursive,
        serialize,
        deserialize,
        attribute_list,
        serde_overrides,
        cls,
    ) = TYPE_BANK[proto.fullyQualifiedName]

    if class_type == type(None):
        # yes this looks stupid but it works and the opposite breaks
        class_type = cls

    if nonrecursive:
        if deserialize is None:
            raise Exception(
                f"Cant serialize {type(proto)} nonrecursive without serialize."
            )

        return deserialize(combine_bytes(proto.nonrecursiveBlob))

    kwargs = {}

    for attr_name, attr_bytes_list in zip(proto.fieldsName, proto.fieldsData):
        attr_bytes = combine_bytes(attr_bytes_list)
        attr_value = _deserialize(attr_bytes, from_bytes=True)
        transforms = serde_overrides.get(attr_name, None)

        if transforms is not None:
            attr_value = transforms[1](attr_value)
        kwargs[attr_name] = attr_value

    if hasattr(class_type, "serde_constructor"):
        return getattr(class_type, "serde_constructor")(kwargs)

    if issubclass(class_type, Enum) and "value" in kwargs:
        obj = class_type.__new__(class_type, kwargs["value"])  # type: ignore
    elif issubclass(class_type, BaseModel):
        # if we skip the __new__ flow of BaseModel we get the error
        # AttributeError: object has no attribute '__fields_set__'

        if "syft.user" in proto.fullyQualifiedName:
            # weird issues with pydantic and ForwardRef on user classes being inited
            # with custom state args / kwargs
            obj = class_type()
            for attr_name, attr_value in kwargs.items():
                setattr(obj, attr_name, attr_value)
        else:
            obj = class_type(**kwargs)

    else:
        obj = class_type.__new__(class_type)  # type: ignore
        for attr_name, attr_value in kwargs.items():
            setattr(obj, attr_name, attr_value)

    return obj


# how else do you import a relative file to execute it?
NOTHING = None
