"""Launch an installed Codex MCP runtime using explicit local configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    default = Path(os.environ.get('LOCALAPPDATA', Path.home()/'.local/share'))/'verdantflare-agent-codex/service.json'
    path = Path(os.environ.get('VERDANTFLARE_AGENT_CODEX_SERVICE', default))
    settings = json.loads(path.read_text('utf-8'))
    command = settings['command']
    if not Path(command).is_absolute():
        raise ValueError('Runtime interpreter must be absolute')
    environment = dict(os.environ)
    environment.update(settings.get('env', {}))
    raise SystemExit(subprocess.call([command, *settings['args'], *sys.argv[1:]], env=environment))


if __name__ == '__main__':
    main()
