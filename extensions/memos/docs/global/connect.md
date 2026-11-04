---
description: Prepare a server URL and personal access token, then connect your Memos client.
outline: false
---

# Connect your app

Prepare `inkcre/memos` on your Core. If you use the Web setup interface, install its matching Web
Distribution and enable it in your browser as well. Installation, Core enablement, and browser
enablement are separate actions; both Hosts share one installed Extension version.

Your app needs two values: the Core server URL ending in `/memos`, and the Memos personal access
token (PAT). Neither is your PostgREST connection setting, and the PAT is not the instance JWT.

<InterfaceGuide>
<template #web>

## Prepare in the Web app

1. Open **Extensions**, locate **Memos**, and open **Setup** from its enabled browser extension.
2. Select the Core that should provide the service if more than one is available. If the public
   address is missing, configure that Core's Public HTTP Base URL in **Peers → Config**, then
   refresh the setup page. Use an address reachable from your phone or other client device.
3. Select **Prepare connection**. If no PAT is saved, the page generates and saves one. If a PAT
   already exists, it keeps it. Memos is enabled on the selected Core only if needed.
4. Copy the displayed **Server URL** and **Personal Access Token**. Keep the token private. The page
   confirms that connection information is ready, not that an external app has connected.

</template>
<template #cli>

## Prepare through an Agent or operator

Ask your Agent or operator to configure `personal_access_token` using the Core Extension management
capability, then enable `inkcre/memos` on the intended Core. The value must be `memos_pat_` followed
by 32 ASCII letters or digits, generated with a cryptographic random generator. Do not reuse a JWT
or another service's password.

The configuration belongs to the deployment's existing Extension record. After enabling Memos,
read its complete Server URL using the `memos.connection.v1` capability on the selected Core.
Do not substitute a PostgREST URL or append `/api/v1`.

</template>
</InterfaceGuide>

## Sign in from the client

1. Open the client application's server sign-in screen.
2. Paste the exact Server URL, including `/memos`.
3. Paste the PAT into its personal-access-token field and sign in.
4. Confirm that the app opens normally. Preparing or copying credentials alone does not test the
   phone's network access or the client's protocol compatibility.

You can now deliberately create a note in the client. The setup interface itself does not create
test notes or collect information. Connecting here does not upload notes kept in an unrelated
account automatically.

## Recover or disconnect

If saving fails, correct the connection problem and retry with the retained draft. If saving
succeeds but Core enablement fails, retry enabling with the same saved PAT. After a timeout, refresh
the actual saved state before trying again.

If client sign-in fails, first check that the device can reach the Core address and that Memos is
enabled there. A browser and a phone may have different network access. Also check the client's
version against the supported baseline; do not add API paths to the URL to guess around an error.

To replace or revoke the PAT, explicitly change Extension Config. Replacing it affects
every client using the old PAT; update those clients manually. Disabling Memos on Core removes its
API routes. Closing the setup page does not disable the service or revoke its token.

Configuration saved successfully applies to subsequent protected Memos requests, without a Core
restart. Requests that already passed authentication are not cancelled by a later revocation.
