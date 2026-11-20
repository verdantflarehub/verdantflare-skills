"""Fixed TOML adapter over the recoverable POSIX config transaction engine.

Separate schemas prevent JSON/TOML record reinterpretation at the same target.
No host configuration discovery, desktop acceptance or Windows ACL is implied.
"""
from __future__ import annotations

from config_transaction import ConfigFile
import host_toml


class TomlConfigFile(ConfigFile):
    schema = 'vf.host-toml-config.transaction/v1'
    planner = host_toml

    def _owned_bytes(self, raw):
        return self.planner.inspect(raw)
