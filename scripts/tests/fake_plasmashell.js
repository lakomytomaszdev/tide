// Atrapa API skryptowego plasmashell (Plasma 6.8) do testów skryptów paneli.
// Uruchamiana przez qml6, czyli w tym samym silniku JS (QJSEngine) co prawdziwy plasmashell.
//
// Odwzorowuje zachowania, na których polegają skrypty Tide:
// - nowy panel powstaje na aktywnym monitorze (0) przy wolnej krawędzi, ekran zmienia setter `screen`,
// - addWidget dopisuje widżet na końcu układu panelu,
// - widget.index czyta AppletOrder z [General] panelu (-1, gdy widżetu tam nie ma),
//   a setter działa jak Widget::setIndex + LayoutManager.reorderApplets/move,
// - panel zapisuje AppletOrder z bieżącego układu dopiero po zakończeniu skryptu (Qt.callLater),
// - widget.globalShortcut działa jak KGlobalAccel: klawisz zajęty przez inną akcję
//   (state.shortcuts: {klawisz: "komponent/akcja"}) nie zostaje przypisany, a odczyt zwraca "".

var __out = "";
var __state = null;
var __objects = {};

function print(text) {
    __out += String(text);
}

function __group(path) {
    return path.length ? "/" + path.join("/") : "/";
}

// Wspólna obsługa konfiguracji: currentConfigGroup, readConfig, writeConfig, configKeys, configGroups.
function __configurable(object, store) {
    var current = [];
    Object.defineProperty(object, "currentConfigGroup", {
        get: function () { return current.slice(); },
        set: function (value) { current = value.slice(); }
    });
    Object.defineProperty(object, "configKeys", {
        get: function () { return Object.keys(store[__group(current)] || {}); }
    });
    Object.defineProperty(object, "configGroups", {
        get: function () {
            var prefix = __group(current) === "/" ? "/" : __group(current) + "/";
            var names = [];
            for (var path in store) {
                if (path !== "/" && path.indexOf(prefix) === 0) {
                    var name = path.substring(prefix.length).split("/")[0];
                    if (names.indexOf(name) < 0) {
                        names.push(name);
                    }
                }
            }
            return names;
        }
    });
    object.readConfig = function (key, fallback) {
        var group = store[__group(current)] || {};
        return group.hasOwnProperty(key) ? group[key] : fallback;
    };
    object.writeConfig = function (key, value) {
        var path = __group(current);
        store[path] = store[path] || {};
        store[path][key] = String(value);
    };
}

function __appletOrder(panel) {
    var raw = (panel.__data.config["/General"] || {}).AppletOrder || "";
    return raw ? raw.split(";").map(Number) : [];
}

function __widget(panel, data) {
    var widget = {id: data.id, type: data.type, __data: data};
    __configurable(widget, data.config);
    Object.defineProperty(widget, "index", {
        get: function () { return __appletOrder(panel).indexOf(data.id); },
        set: function (index) {
            // Widget::setIndex: kolejność z AppletOrder, wstawienie, reorderApplets(order).
            var order = __appletOrder(panel).filter(function (id) { return id !== data.id; });
            order.splice(Math.min(Math.max(0, index), order.length - 1), 0, data.id);
            panel.__reorder(order);
        }
    });
    Object.defineProperty(widget, "geometry", {
        get: function () { return {x: panel.__data.layout.indexOf(data.id) * 40, y: 4, width: 36, height: 28}; }
    });
    Object.defineProperty(widget, "globalShortcut", {
        get: function () { return data.shortcut || ""; },
        set: function (key) {
            var own = "plasmashell/activate widget " + data.id;
            var shortcuts = __state.shortcuts = __state.shortcuts || {};
            for (var taken in shortcuts) {
                if (shortcuts[taken] === own) {
                    delete shortcuts[taken];
                }
            }
            data.shortcut = "";
            if (key && !shortcuts.hasOwnProperty(key)) {
                shortcuts[key] = own;
                data.shortcut = key;
            }
        }
    });
    widget.remove = function () {
        panel.__data.layout = panel.__data.layout.filter(function (id) { return id !== data.id; });
        data.removed = true;
    };
    return widget;
}

function __panel(data) {
    if (__objects[data.id]) {
        return __objects[data.id];
    }
    var panel = {id: data.id, type: "org.kde.panel", __data: data};
    __configurable(panel, data.config);
    ["location", "height", "floating", "alignment", "lengthMode", "hiding", "opacity", "offset",
     "minimumLength", "maximumLength"].forEach(function (name) {
        Object.defineProperty(panel, name, {
            get: function () { return data[name]; },
            set: function (value) { data[name] = value; data.log.push(name + "=" + value); }
        });
    });
    Object.defineProperty(panel, "screen", {
        get: function () { return data.screen; },
        set: function (value) { data.screen = value; data.log.push("screen=" + value); }
    });
    panel.widgets = function () {
        // Kolejność tworzenia, nie kolejność na panelu — jak containment->applets().
        return data.widgets.filter(function (w) { return !w.removed; })
                           .sort(function (a, b) { return a.id - b.id; })
                           .map(function (w) { return __widget(panel, w); });
    };
    panel.widgetById = function (id) {
        var found = data.widgets.filter(function (w) { return w.id === id && !w.removed; });
        return found.length ? __widget(panel, found[0]) : null;
    };
    panel.addWidget = function (type) {
        var w = {id: __state.nextId++, type: type, config: {}, removed: false};
        data.widgets.push(w);
        data.layout.push(w.id);
        return __widget(panel, w);
    };
    panel.remove = function () {
        data.removed = true;
    };
    panel.__reorder = function (order) {
        // LayoutManager.reorderApplets + move: element trafia przed pozycję `end`.
        var i = 0;
        order.forEach(function (id) {
            var start = data.layout.indexOf(id);
            if (start >= 0) {
                var target = i - (start < i ? 1 : 0);
                data.layout.splice(start, 1);
                data.layout.splice(target, 0, id);
            }
            ++i;
        });
        panel.__save();
    };
    panel.__save = function () {
        data.config["/General"] = data.config["/General"] || {};
        data.config["/General"].AppletOrder = data.layout.join(";");
    };
    __objects[data.id] = panel;
    return panel;
}

var screenCount = 0;

// Geometria ekranu: state.geometries[numer] albo 1920×1080.
function screenGeometry(screen) {
    var all = __state.geometries || [];
    return all[screen] || {x: 1920 * screen, y: 0, width: 1920, height: 1080};
}

function screenForConnector(connector) {
    return __state.screens.indexOf(connector);
}

function panels() {
    return __state.panels.filter(function (p) { return !p.removed; }).map(__panel);
}

function panelById(id) {
    var found = __state.panels.filter(function (p) { return p.id === id && !p.removed; });
    return found.length ? __panel(found[0]) : null;
}

// Aktywności i pulpity (state.currentActivity, state.desktops: [{id, screen, activity, widgets}]).
// desktopsForActivity zakłada pulpity aktywności, która ich jeszcze nie ma (jak w Plasmie 6.8) —
// state.createdDesktopsFor zapisuje, dla kogo.
function currentActivity() {
    return __state.currentActivity || "";
}

function __desktop(data) {
    var desktop = {id: data.id, screen: data.screen, type: data.type || "org.kde.plasma.folder", __data: data};
    desktop.widgets = function () {
        // Widget.remove() zdejmuje aplet z pulpitu (state.removedFromDesktops: [{desktop, id, type}]).
        return data.widgets.map(function (w) {
            return {id: w.id, type: w.type, geometry: w.geometry, remove: function () {
                data.widgets = data.widgets.filter(function (x) { return x.id !== w.id; });
                __state.removedFromDesktops = (__state.removedFromDesktops || []).concat(
                    [{desktop: data.id, id: w.id, type: w.type}]);
            }};
        });
    };
    desktop.addWidget = function (type, x, y, w, h) {
        var widget = {id: __state.nextId++, type: type, geometry: [x, y, w, h]};
        data.widgets.push(widget);
        return {id: widget.id, type: type};
    };
    return desktop;
}

function desktops() {
    return (__state.desktops || []).map(__desktop);
}

function desktopsForActivity(activity) {
    __state.desktops = __state.desktops || [];
    var mine = __state.desktops.filter(function (d) { return d.activity === activity; });
    if (!mine.length) {
        __state.createdDesktopsFor = (__state.createdDesktopsFor || []).concat([activity]);
        for (var s = 0; s < __state.screens.length; ++s) {
            var data = {id: __state.nextId++, screen: s, activity: activity, widgets: []};
            __state.desktops.push(data);
            mine.push(data);
        }
    }
    return mine.map(__desktop);
}

function Panel(plugin) {
    // ShellCorona::addPanel: aktywny monitor (tu 0) i pierwsza wolna krawędź.
    var used = __state.panels.filter(function (p) { return !p.removed && p.screen === 0; })
                             .map(function (p) { return p.location; });
    var edge = ["bottom", "top", "left", "right"].filter(function (e) { return used.indexOf(e) < 0; })[0] || "top";
    var data = {id: __state.nextId++, screen: 0, location: edge, height: 44, floating: false,
                alignment: "center", lengthMode: "fill", hiding: "none", opacity: "adaptive", offset: 0,
                minimumLength: 0, maximumLength: 0, config: {"/": {plugin: plugin}}, widgets: [], layout: [],
                log: ["created"], removed: false};
    __state.panels.push(data);
    return __panel(data);
}

function run(state, script) {
    __state = state;
    screenCount = state.screens.length;
    script();
    // Po skrypcie panel zapisuje AppletOrder z układu (LayoutManager.save przez Qt.callLater).
    state.panels.forEach(function (p) {
        if (!p.removed) {
            __panel(p).__save();
        }
    });
    return JSON.stringify({out: __out, state: state});
}
