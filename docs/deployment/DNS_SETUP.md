# DNS for ryzeaibiapp.com

At the registrar or DNS provider for `ryzeaibiapp.com`, create:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `@` (ryzeaibiapp.com) | `<RESERVED_PUBLIC_IP>` | 300 |
| A | `www` | `<RESERVED_PUBLIC_IP>` | 300 |

- Remove any other A, AAAA or CNAME records for `@` and `www` (for example a registrar parking page).
  A stale AAAA record makes certificate issuance fail.
- If the DNS provider offers a proxy (for example Cloudflare's orange cloud), keep it **off**
  ("DNS only") for the first deployment so Caddy can obtain its certificates directly.
- If you have a CAA record, it must allow `letsencrypt.org` (and `pki.goog` or `zerossl.com`,
  Caddy's fallbacks).

Check propagation from your PC:

```powershell
Resolve-DnsName www.ryzeaibiapp.com -Type A
Resolve-DnsName ryzeaibiapp.com -Type A
```

Both must return the reserved IP before the first deployment. Caddy then obtains and renews the
certificates automatically; ports 80 and 443 must be open (see ORACLE_CLOUD_SETUP.md step 2).

Result:

- `https://www.ryzeaibiapp.com` serves the app; `/api/*` reaches the API.
- `http://` and `https://ryzeaibiapp.com` redirect permanently to `https://www.ryzeaibiapp.com`.
