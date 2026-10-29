# Domain and delivery setup

Assume your addresses use `example.com`, your server is `mail.example.com`, and its public IPv4 is `203.0.113.10`. These are documentation examples: replace them.

| Type | Name | Value | Where |
| --- | --- | --- | --- |
| A | `mail.example.com` | Your public IPv4 | DNS provider |
| MX | `example.com` | Priority 10, `mail.example.com` | DNS provider |
| TXT | `example.com` | `v=spf1 mx -all` | DNS provider |
| TXT | `mail._domainkey.example.com` | Full DKIM value from Morrow | DNS provider |
| TXT | `_dmarc.example.com` | `v=DMARC1; p=none; adkim=s; aspf=s` | DNS provider |
| PTR | Your public IP | `mail.example.com` | VPS/IP provider |

Some providers expect relative names: `mail`, `@`, `mail._domainkey`, `_dmarc`. Others accept the full names. Do not accidentally create `mail.example.com.example.com`. An MX value is a hostname, never an IP, URL, or CNAME. Use a short TTL such as 300 during setup.

The mail hostname must resolve directly to the server: turn off CDN/HTTP proxying. The same hostname is used for HTTPS, SMTP, IMAP, TLS, and outbound SMTP identification. Do not set the mail hostname equal to the mail domain.

## Before running the installer

- Confirm the A record resolves to the server and inbound port 80 can reach it. Let's Encrypt HTTP-01 uses port 80; keep it open for renewal.
- Allow inbound TCP 25 (server mail), 80/443 (web and certificate renewal), 587 (authenticated submission), and 993 (IMAP TLS).
- Confirm outbound TCP 25 is permitted. Many cloud and residential networks block it. A correct web interface cannot overcome that provider restriction.
- Request the PTR record from the provider that allocated the IP. Forward and reverse DNS should agree.
- Keep a working external contact address for provider access and alerts. The mail administrator's address will not receive certificate notices until DNS and delivery work.

The app's **Edit public IP** changes its DNS instructions. It does not reconfigure a network interface, change provider records, or rename the server. A public IP change requires updating both the A and PTR records.

## SPF, DKIM, and DMARC

Publish **one SPF record** per domain. `v=spf1 mx -all` authorizes the domain's MX host and excludes other sources. If another service sends mail for your domain, merge its approved mechanisms into the existing SPF policy before enforcing it. Do not blindly add a second SPF record.

Morrow creates a 2048-bit RSA DKIM key and uses the selector `mail`. All hosted domains share that installation's key, with a domain-specific `d=` signature. Publish the full displayed TXT value for every domain. Many DNS providers automatically split long TXT values into quoted chunks; that is normal, but all chunks must belong to the same TXT record.

DMARC begins at `p=none` to allow staged setup. Verify your real messages' SPF, DKIM, and alignment with an external recipient before switching to `p=quarantine` or `p=reject`. If you add reporting addresses (`rua`), ensure the mailbox exists and you can process those reports; Morrow does not parse aggregate reports. Strict alignment is the supplied default, so mail must use its actual hosted sending domain.

New domains can be added from the web interface without a service restart. Postfix looks up domains and identities from SQLite. An administrator may add domains they control; Morrow does not automatically prove ownership, register a domain, or edit a DNS provider account. Add `postmaster` and `abuse` receiving aliases for each extra domain. The initial domain gets those aliases automatically.

## IPv6

The default sends over IPv4 only. Add `--ipv6` at installation only when the host has a stable public IPv6, correct routing, working inbound/outbound TCP 25, and matching IPv6 PTR. Then publish its AAAA record and set reverse DNS for **both** addresses. Do not publish a broken AAAA record: other servers may prefer it and fail to deliver.

## Verify real delivery

1. In Morrow, use **Check published DNS**. This compares the displayed expected records with the host's resolver. A matching record is not a reputation or delivery test.
2. From another machine, test `openssl s_client -connect mail.example.com:993 -servername mail.example.com` and `openssl s_client -starttls smtp -connect mail.example.com:587 -servername mail.example.com`.
3. Send a message from an external mailbox to a local member and reply. Check headers at the external recipient for SPF/DKIM/DMARC results. Test receiving through an alias too.
4. Confirm a submission from an ungranted address is rejected and unauthenticated relay to a non-local address is denied.
5. Inspect `postqueue -p` and the Postfix journal for deferrals. An SMTP `250` means a server accepted the message, not that a recipient placed it in the inbox.

Morrow does not send external test messages automatically. Reputation, provider policies, recipient filters, and IP blocklists can still prevent delivery even with correct DNS. This release does not automate a third-party outbound relay.

References: [Postfix SMTP access controls](https://www.postfix.org/SMTPD_ACCESS_README.html), [Postfix sender ownership](https://www.postfix.org/postconf.5.html#smtpd_sender_login_maps), [Let's Encrypt HTTP-01](https://letsencrypt.org/docs/challenge-types/#http-01-challenge), [OpenDKIM configuration on Ubuntu 24.04](https://manpages.ubuntu.com/manpages/noble/man5/opendkim.conf.5.html).
