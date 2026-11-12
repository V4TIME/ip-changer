# Security and privacy notes

## What this tool does

IP Changer asks a **local Tor daemon** to build new circuits (`SIGNAL NEWNYM`)
and verifies the Tor exit IP through Tor's SOCKS port. It rotates the apparent
IP **for traffic that is sent through Tor**. It does not alter your network
interface, your ISP-assigned address, or applications that ignore the proxy.

## What it does not do

- It does not hide all system traffic. Configure each application to use
  `socks5h://127.0.0.1:9050` (or a system-wide Tor transparent proxy) if that is
  what you need.
- It does not defeat every blocklist. Many sites block known Tor exits.
- It does not make illegal activity safe or acceptable. You are responsible for
  complying with the law and with the terms of services you use.

## Threat model

This is a local privacy convenience utility, not an anonymity guarantee.

- **Tor exit node:** sees the destination and unencrypted traffic; use HTTPS.
- **Local user/process:** can read the config if it can read your home. The
  config is written `0600` and the containing directory `0700`.
- **Root:** can read everything; the installer avoids running the daemon as
  root when a normal user account is available.
- **State/logs:** contain exit IPs and timestamps. Use `--purge` when
  uninstalling if that history matters to you.
- **Control password:** if you configure `auth_method=password`, the password
  is stored in the JSON config. Prefer cookie authentication. The CLI and the
  panel redact the password when displaying configuration, but the file itself
  is not encrypted.

## Installer safety

- torrc is backed up before modification and validated with
  `tor --verify-config` when Tor is present.
- Only a marked block is added/removed; the rest of torrc is left alone.
- The systemd unit runs as the invoking user, with `NoNewPrivileges=true`,
  `PrivateTmp=true`, and read-only home/system paths except the config/state
  directories. It never executes a script from a user-writable install path.
- The daemon takes an exclusive `flock` around rotations, so two clients cannot
  send NEWNYM at the same time.
- The CLI rejects intervals below 10 seconds instead of silently hammering Tor.

## Reporting a problem

Open an issue with the output of:

```bash
ipchanger --version
ipchanger doctor --quick
ipchanger status --json
```

Remove any IP addresses you do not want to publish.
