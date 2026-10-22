"""Optional StatusNotifierItem, using Gio D-Bus without GTK3 or a daemon."""
from gi.repository import Gio, GLib
from .resources import APP_ICON_NAME

XML = '''<node><interface name="org.kde.StatusNotifierItem">
<method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<property name="Category" type="s" access="read"/>
<property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<property name="IconName" type="s" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/>
<signal name="NewTitle"/><signal name="NewStatus"><arg type="s"/></signal>
</interface></node>'''


class TrayIndicator:
    def __init__(self, window):
        self.window = window
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.registration = 0
        self.available = False
        self.title = "RAMPilot"
        self.watch = Gio.bus_watch_name_on_connection(
            self.bus, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            self.appeared, self.vanished)

    def appeared(self, connection, name, owner):
        if not self.window.config.show_tray:
            return
        if not self.registration:
            interface = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
            self.registration = self.bus.register_object("/StatusNotifierItem", interface, self.method, self.property, None)
        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                      "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                      GLib.Variant("(s)", (self.bus.get_unique_name(),)), None,
                      Gio.DBusCallFlags.NONE, 2000, None, self.registered)

    def registered(self, connection, result):
        try:
            connection.call_finish(result)
            self.available = True
        except GLib.Error:
            self.available = False

    def vanished(self, *_):
        self.available = False
        if not self.window.stopped:
            self.window.present()

    def method(self, connection, sender, path, interface, method, params, invocation):
        self.window.present()
        invocation.return_value(None)

    def property(self, connection, sender, path, interface, prop):
        values = {"Category": "SystemServices", "Id": "rampilot", "Title": self.title,
                  "Status": "Active", "IconName": APP_ICON_NAME}
        if prop in values:
            return GLib.Variant("s", values[prop])
        return GLib.Variant("b", False) if prop == "ItemIsMenu" else GLib.Variant("o", "/")

    def update(self, percent):
        self.title = f"RAMPilot · {percent:.0f}% RAM in use"
        if self.registration:
            self.bus.emit_signal(None, "/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewTitle", None)

    def close(self):
        Gio.bus_unwatch_name(self.watch)
        if self.registration:
            self.bus.unregister_object(self.registration)
