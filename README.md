# VerdantFlare Skills

This repository contains the `verdantflare-video` Skill for Codex. It creates SD2 video tasks through the VerdantFlare API, uploads local references to the configured temporary bucket, tracks asynchronous status, and downloads completed results.

Install the Skill from an immutable Release tag, then run:

```bash
bash "${CODEX_HOME:-$HOME/.codex}/skills/verdantflare-video/scripts/install-config-macos.sh"
```

The installer reads the one-time configuration URL from a hidden TTY prompt. The downloaded configuration must contain these three required fields:

```dotenv
VERDANTFLARE_VIDEO_API_KEY=<required>
VERDANTFLARE_VIDEO_S3_ACCESS_KEY=<required>
VERDANTFLARE_VIDEO_S3_SECRET_KEY=<required>
```

All other supported fields are optional and use release defaults. Users do not need to install `mc`, configure an `mc` alias, or edit `.env`; the installer downloads and verifies the pinned MinIO `mc` Release into the Skill state directory when needed.
