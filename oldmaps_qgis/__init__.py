def classFactory(iface):
    from .oldmaps_plugin import OldMapsPlugin

    return OldMapsPlugin(iface)
