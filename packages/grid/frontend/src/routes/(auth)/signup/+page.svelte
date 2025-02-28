<script lang="ts">
  import { goto } from '$app/navigation';
  import { metadata } from '$lib/store';
  import Button from '$lib/components/Button.svelte';
  import DomainMetadataPanel from '$lib/components/authentication/DomainMetadataPanel.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Input from '$lib/components/Input.svelte';
  import { getClient } from '$lib/store';
  import { register } from '$lib/api/auth';

  async function createUser({
    email,
    password,
    confirm_password,
    fullName,
    organization,
    website
  }) {
    if (password.value !== confirm_password.value) {
      throw Error('Password and password confirmation mismatch');
    }

    let newUser = {
      email: email.value,
      password: password.value,
      password_verify: confirm_password.value,
      name: fullName.value,
      institution: organization.value,
      website: website.value
    };

    // Filter attributes that doesn't exist
    Object.keys(newUser).forEach((k) => newUser[k] == '' && delete newUser[k]);

    try {
      await register(newUser); // This will return a success message and the new user info
      goto('/login');
    } catch (e) {
      console.error(e);
    }
  }
</script>

<div class="flex flex-col xl:flex-row w-full h-full xl:justify-around items-center gap-12">
  <DomainMetadataPanel metadata={$metadata} />
  <form class="contents" on:submit|preventDefault={(e) => createUser(e.target)}>
    <Modal>
      <div
        class="flex flex-shrink-0 justify-between p-4 pb-0 flex-nowrap w-full h-min"
        slot="header"
      >
        <span class="block text-center w-full">
          <p class="text-2xl font-bold text-gray-800">Apply for an account</p>
        </span>
      </div>
      <div class="contents" slot="body">
        <div class="w-full gap-6 flex flex-col tablet:flex-row">
          <Input label="Full name" id="fullName" placeholder="Jane Doe" required />
          <Input label="Company/Institution" id="organization" placeholder="OpenMined University" />
        </div>
        <Input label="Email" id="email" placeholder="info@openmined.org" required />
        <div class="w-full gap-6 flex flex-col tablet:flex-row">
          <Input type="password" label="Password" id="password" placeholder="******" required />
          <Input
            type="password"
            label="Confirm Password"
            id="confirm_password"
            placeholder="******"
            required
          />
        </div>
        <Input label="Website/Profile" id="website" placeholder="https://openmined.org" />
        <p class="text-center">
          Already have an account? Sign in <a
            class="text-primary-600 underline hover:opacity-50"
            href="/login"
          >
            here
          </a>
          .
        </p>
      </div>
      <Button type="submit" variant="secondary" slot="button-group">Sign up</Button>
    </Modal>
  </form>
</div>
