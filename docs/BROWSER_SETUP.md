# Connect Chrome, Firefox and other apps to Tor

IP Changer rotates the **Tor exit IP**. For a browser or app to get that new IP,
it must send its traffic through Tor's SOCKS proxy. The default is:

```text
127.0.0.1:9050
```

This guide connects the most common browsers and apps to it.

> **First, confirm Tor works:**
>
> ```bash
> ipchanger doctor
> ipchanger ip
> ```
>
> `ipchanger ip` should print an IP and the doctor should show `[ OK ]` for the
> Tor binary, control port and SOCKS port. If not, fix that before touching the
> browser.

## Desktop Chrome, Chromium, Edge and Brave

Chrome does not have a permanent “SOCKS proxy” checkbox in its normal settings.
The reliable way is to launch it with a dedicated profile and the proxy flag.

### Linux

```bash
google-chrome \
  --proxy-server="socks5://127.0.0.1:9050" \
  --user-data-dir="$HOME/.config/chrome-tor"
```

Use the matching binary name for your browser:

| Browser | Command |
|---------|---------|
| Google Chrome | `google-chrome` |
| Chromium | `chromium` or `chromium-browser` |
| Microsoft Edge | `microsoft-edge` |
| Brave | `brave-browser` |

### macOS

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --proxy-server="socks5://127.0.0.1:9050" \
  --user-data-dir="$HOME/.config/chrome-tor"
```

For Brave or Edge, replace the application path with the matching `.app`.

### What the flags do

- `--proxy-server="socks5://127.0.0.1:9050"` sends HTTP/HTTPS traffic through
  Tor's SOCKS5 port.
- `--user-data-dir=...` gives Tor browsing its own profile, so your normal Chrome
  windows keep using your direct connection. Close the Tor window when you are
  done; do not sign into personal accounts in it unless you intend to.

### Verify

Open <https://check.torproject.org/> in the Tor window. You should see:

```text
Congratulations. This browser is configured to use Tor.
```

Then run:

```bash
ipchanger rotate
```

Refresh the page. The exit IP should change (Tor may occasionally reuse an exit,
so try the next rotation if it does not).

### Chrome on Windows

IP Changer itself targets Linux, macOS and Termux. If you run Tor separately on
Windows, the same flag works from PowerShell:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --proxy-server="socks5://127.0.0.1:9050" `
  --user-data-dir="$env:TEMP\chrome-tor"
```

## Firefox (desktop)

Firefox has a built-in SOCKS setting:

1. Open **Settings → General → Network Settings → Settings…**
2. Select **Manual proxy configuration**.
3. **SOCKS Host:** `127.0.0.1`
4. **Port:** `9050`
5. Select **SOCKS v5**.
6. Tick **Proxy DNS when using SOCKS v5**.
7. Click **OK**, then open <https://check.torproject.org/>.

Firefox can keep a separate profile for Tor browsing:

```bash
firefox --new-instance --profile "$HOME/.mozilla/firefox/tor-profile"
```

Create the profile once with `firefox --CreateProfile "tor-profile $HOME/.mozilla/firefox/tor-profile"`.

## Android and Termux

Android Chrome **cannot** be pointed at a SOCKS proxy directly. The practical
options are:

### 1. Orbot in VPN mode (recommended for Chrome)

Orbot routes Android apps through Tor using Android's VPN API. Install Orbot,
enable **VPN mode**, and select Chrome (or all apps). Chrome then uses Tor without
any browser settings.

If you also want IP Changer to rotate that Tor instance, enable Orbot's control
port (if your Orbot build exposes it) and configure IP Changer accordingly:

```bash
ipchanger config set control_port 9051
ipchanger config set auth_method auto
ipchanger doctor
```

Orbot versions differ; if the control port is unavailable, use Orbot's own
**New Identity** action instead.

### 2. A SOCKS5-capable browser or VPN front-end

Some Android browsers and proxy/VPN apps accept a SOCKS5 endpoint. Point them at
`127.0.0.1:9050` **only if** the app runs in the same network namespace as the
Termux Tor daemon. Most normal Android apps cannot reach Termux's localhost, so
VPN mode is usually the only clean solution.

### 3. Test from a desktop browser

The simplest way to verify Termux rotation is to run Tor + IP Changer in Termux,
then use a desktop browser on the same machine/network with an SSH tunnel or a
SOCKS-capable client. This avoids Android's per-app proxy limitations.

### Termux command-line apps

If the app supports SOCKS5, use `127.0.0.1:9050`. For apps that do not, use a
wrapper such as `torsocks` (install it with `pkg install torsocks` if available):

```bash
torsocks curl https://check.torproject.org/api/ip
```

## Any command-line program

### curl

```bash
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
```

`--socks5-hostname` lets the proxy resolve DNS, which avoids a local DNS leak.

### torsocks

```bash
torsocks curl https://check.torproject.org/api/ip
torsocks ssh user@example.com
```

### proxychains

Add this to `~/.proxychains/proxychains.conf`:

```ini
[ProxyList]
socks5 127.0.0.1 9050
```

Then:

```bash
proxychains4 curl https://check.torproject.org/api/ip
```

## System-wide proxy

Most desktops let you set a SOCKS proxy in **Settings → Network → Proxy**. Use
`127.0.0.1` port `9050` and SOCKS v5. Not every app honours system proxy
settings, so verify with the app you actually care about.

For a terminal-only session, `torsocks` or `proxychains` is usually more
predictable.

## Common mistakes

| Mistake | Result | Fix |
|---------|--------|-----|
| Rotating circuits but browsing without a proxy | IP never changes | Launch the browser with the SOCKS flag or use Orbot VPN mode. |
| Using HTTP proxy settings on port 9050 | Connection fails | 9050 is **SOCKS5**, not HTTP. |
| Using port 9051 in the browser | Connection fails | 9051 is the **control port**; browsers use 9050. |
| Chrome window opened without the flag | Normal direct connection | Use the dedicated `--user-data-dir` window. |
| Android Chrome expecting SOCKS settings | No such setting | Use Orbot VPN mode or a proxy/VPN front-end. |
| Checking the IP in a different browser | Old IP shown | Verify in the exact browser/profile using Tor. |

## Privacy notes

- Tor hides your IP from the destination site, but the site can still fingerprint
  your browser. Use a dedicated profile and avoid logging into personal accounts.
- WebRTC can reveal local network addresses in some configurations. If that
  matters to you, use a WebRTC-blocking extension or disable WebRTC in the Tor
  profile.
- DNS: with Chrome's SOCKS5 flag, hostnames are sent to the proxy. With Firefox,
  enable **Proxy DNS when using SOCKS v5**. Test at <https://dnsleaktest.com/>.
- Do not use this to evade bans, commit fraud, or break the law. Tor is for
  privacy, research and censorship resistance.
