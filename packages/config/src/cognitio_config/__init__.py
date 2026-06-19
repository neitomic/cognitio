"""cognitio-config — Layer 0.

Typed application settings shared by the composition roots in ``packages/api`` and
``apps/worker``. No other Cognitio package depends on this module's siblings: it is the
bottom of the dependency graph so any layer may read configuration without reaching
upward and without calling ``os.environ`` directly.

Public surface:
    - ``Settings`` : the validated settings model.
    - ``get_settings`` : cached accessor used by composition roots.
"""

from cognitio_config.settings import Settings, get_settings

__all__ = ["Settings", "get_settings"]
