// A filter applied as soon as it changes.
(function () {
    "use strict";

    document.querySelectorAll("[data-autosubmit]").forEach(function (input) {
        input.addEventListener("change", function () {
            input.form.submit();
        });
    });
}());

// The discs and groupings of an album's tracks, folded until asked for; without script, they stay so.
(function () {
    "use strict";

    document.querySelectorAll("[data-shows-discs]").forEach(function (button) {
        var table = document.getElementById(button.dataset.showsDiscs);
        button.hidden = false;
        button.addEventListener("click", function () {
            table.classList.add("discs", "groupings");
            table.querySelectorAll("[data-full-row]").forEach(function (cell) {
                cell.colSpan = table.tHead.rows[0].cells.length;
            });
            button.hidden = true;
        });
    });
}());

// On a phone, a track's card folds to its number and title; one holding a repeated value or to sort
// out starts open.
(function () {
    "use strict";

    document.querySelectorAll("[data-track-more]").forEach(function (button) {
        var row = button.closest("tr");
        function show(open) {
            row.classList.toggle("folded", !open);
            button.setAttribute("aria-expanded", open ? "true" : "false");
        }
        button.hidden = false;
        show(row.querySelector("input.repeated") !== null || row.classList.contains("to-sort"));
        button.addEventListener("click", function () {
            show(row.classList.contains("folded"));
        });
    });
}());

// The values of an album field whose tracks differ: one highlights its tracks while pointed at or
// chosen, and choosing it puts it in the field, all but the empty value.
(function () {
    "use strict";

    var buttons = document.querySelectorAll("[data-spread]");
    var chosen = null;

    function mark(button, on) {
        button.dataset.rows.split(" ").forEach(function (row) {
            var tr = document.querySelector("tr[data-row='" + row + "']");
            if (tr) {
                tr.classList.toggle("spread-hit", on);
            }
        });
    }

    function show(button) {
        document.querySelectorAll("tr.spread-hit").forEach(function (tr) {
            tr.classList.remove("spread-hit");
        });
        if (button) {
            mark(button, true);
        }
    }

    buttons.forEach(function (button) {
        button.addEventListener("mouseenter", function () { show(button); });
        button.addEventListener("focus", function () { show(button); });
        button.addEventListener("mouseleave", function () { show(chosen); });
        button.addEventListener("blur", function () { show(chosen); });
        button.addEventListener("click", function () {
            var field = button.form.elements.namedItem(button.dataset.spread);
            buttons.forEach(function (other) { other.removeAttribute("aria-pressed"); });
            chosen = chosen === button ? null : button;
            if (chosen) {
                button.setAttribute("aria-pressed", "true");
                if (button.dataset.value) {
                    field.value = button.dataset.value;
                }
            }
            show(chosen);
        });
    });
}());

// A pass card opens its options in a dialog, each warning shown while its box makes the pass long;
// without script, the card submits the options as ticked by default.
(function () {
    "use strict";

    document.querySelectorAll("[data-opens-dialog]").forEach(function (opener) {
        var form = opener.form;
        var dialog = form.querySelector("dialog");
        var warnings = dialog.querySelectorAll("[data-long]");

        function update() {
            warnings.forEach(function (warning) {
                var box = form.elements.namedItem(warning.dataset.long);
                warning.hidden = box.checked !== (warning.dataset.longWhen === "checked");
            });
        }

        opener.addEventListener("click", function (event) {
            event.preventDefault();
            if (!dialog.open) {
                update();
                dialog.showModal();
            }
        });
        dialog.addEventListener("change", update);
        dialog.addEventListener("close", function () { form.reset(); });
        dialog.querySelector("[data-closes-dialog]").addEventListener("click", function () {
            dialog.close();
        });
    });
}());

// The parts of a page kept up to date (the passes running, the To do tiles); an open panel stays open.
(function () {
    "use strict";

    var REFRESH_MS = 5000;

    function refresh(slot) {
        fetch(slot.dataset.refresh)
            .then(function (response) {
                if (!response.ok) {
                    throw new Error(response.statusText);
                }
                return response.text();
            })
            .then(function (html) {
                var open = slot.querySelector("details[open]") !== null;
                slot.innerHTML = html;
                var details = slot.querySelector("details");
                if (details && open) {
                    details.open = true;
                }
            })
            .catch(function () { return null; })
            .then(function () { setTimeout(refresh, REFRESH_MS, slot); });
    }

    document.querySelectorAll("[data-refresh]").forEach(function (slot) {
        setTimeout(refresh, REFRESH_MS, slot);
    });
}());

// A page showing lists still being built moves its progress bars, and reloads once they are built,
// unless something was typed, ticked or unfolded meanwhile.
(function () {
    "use strict";

    var POLL_MS = 2000;
    var marker = document.querySelector("[data-reload-when-built]");
    if (!marker) {
        return;
    }
    var url = marker.dataset.reloadWhenBuilt;
    var bars = document.querySelectorAll("[data-build-progress]");
    var touched = false;

    function touch() {
        touched = true;
    }
    document.addEventListener("input", touch);
    document.addEventListener("toggle", touch, true);

    function poll() {
        if (touched) {
            return;
        }
        fetch(url)
            .then(function (response) { return response.json(); })
            .then(function (data) {
                bars.forEach(function (bar) {
                    bar.value = data.percent;
                    bar.textContent = data.percent + " %";
                });
                if (touched) {
                    return;
                }
                if (data.ready) {
                    window.location.reload();
                } else {
                    setTimeout(poll, POLL_MS);
                }
            })
            .catch(function () { setTimeout(poll, POLL_MS * 5); });
    }

    setTimeout(poll, POLL_MS);
}());

// The output of a running pass, appended as it is written, and its duration, until the pass ends.
(function () {
    "use strict";

    var POLL_INTERVAL_MS = 2000;
    var log = document.getElementById("log");
    if (!log || !log.dataset.live) {
        return;
    }
    var url = log.dataset.live;
    var offset = Number(log.dataset.from) || 0;
    var kind = log.dataset.kind || "";
    var duration = document.getElementById("run-duration");

    function atBottom() {
        return log.scrollHeight - log.scrollTop - log.clientHeight < 24;
    }

    function append(lines) {
        var follow = atBottom();
        lines.forEach(function (line) {
            var span = document.createElement("span");
            span.className = line.kind ? "line " + line.kind : "line";
            span.textContent = line.text;
            log.appendChild(span);
            log.appendChild(document.createTextNode("\n"));
        });
        if (follow) {
            log.scrollTop = log.scrollHeight;
        }
    }

    function poll() {
        fetch(url + "?from=" + offset + "&kind=" + encodeURIComponent(kind))
            .then(function (response) { return response.json(); })
            .then(function (data) {
                append(data.lines);
                offset = data.next;
                if (duration && data.duration) {
                    duration.textContent = data.duration;
                }
                if (data.lines.length) {
                    kind = data.lines[data.lines.length - 1].kind;
                }
                if (data.status === "running") {
                    setTimeout(poll, POLL_INTERVAL_MS);
                } else {
                    window.location.reload();
                }
            })
            .catch(function () {
                setTimeout(poll, POLL_INTERVAL_MS * 5);
            });
    }

    log.scrollTop = log.scrollHeight;
    setTimeout(poll, POLL_INTERVAL_MS);
}());

// The instrumental box puts the mark in its track's lyrics and locks them; unticked, the text it
// replaced comes back.
(function () {
    "use strict";

    document.querySelectorAll("[data-instrumental]").forEach(function (box) {
        var text = box.form.elements.namedItem(box.dataset.target);
        var mark = box.dataset.instrumental;
        var kept = "";

        function update() {
            if (box.checked && text.value !== mark) {
                kept = text.value;
                text.value = mark;
            } else if (!box.checked && text.value === mark) {
                text.value = kept;
            }
            text.readOnly = box.checked;
        }

        box.addEventListener("change", update);
        update();
    });
}());

// Leaving an album's page with changes neither applied nor discarded asks first: a field changed, an image
// picked, or a preview shown. The browser words the question; sending the form or discarding asks nothing.
(function () {
    "use strict";

    var form = document.getElementById("album-form");
    if (!form) {
        return;
    }
    var leaving = false;

    function changed(el) {
        if (el.type === "file") {
            return el.files.length > 0;
        }
        if (el.type === "checkbox" || el.type === "radio") {
            return el.checked !== el.defaultChecked;
        }
        if (el.tagName === "SELECT") {
            return Array.prototype.some.call(el.options, function (o) { return o.selected !== o.defaultSelected; });
        }
        return el.type !== "hidden" && "defaultValue" in el && el.value !== el.defaultValue;
    }

    form.addEventListener("submit", function () { leaving = true; });
    document.querySelectorAll("[data-discards]").forEach(function (link) {
        link.addEventListener("click", function () { leaving = true; });
    });
    window.addEventListener("beforeunload", function (event) {
        if (!leaving && (form.hasAttribute("data-unsaved") || Array.prototype.some.call(form.elements, changed))) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
}());

// The cover chosen for the album form shows in place of the album's until the form is sent.
(function () {
    "use strict";

    document.querySelectorAll("[data-cover-file]").forEach(function (input) {
        var shown = input.parentElement.querySelector("[data-cover-shown]");
        var url = null;

        input.addEventListener("change", function () {
            var file = input.files[0];
            if (!file) {
                return;
            }
            if (url) {
                URL.revokeObjectURL(url);
            }
            url = URL.createObjectURL(file);
            shown.src = url;
        });
    });
}());

// A track's lyrics open under it from their cell; those changed in the form stay open.
(function () {
    "use strict";

    document.querySelectorAll("[data-lyrics-toggle]").forEach(function (button) {
        var row = document.getElementById(button.dataset.lyricsToggle);
        if (!row) {
            return;
        }
        function show(open) {
            row.hidden = !open;
            button.setAttribute("aria-expanded", open ? "true" : "false");
        }
        show(row.hasAttribute("data-open"));
        button.addEventListener("click", function () {
            show(row.hidden);
            if (!row.hidden) {
                row.querySelector("textarea").focus();
            }
        });
    });
}());

// Look up online puts what the providers have for the track, as typed in the form, in its lyrics box,
// to preview with the rest of the form; the instrumental box follows.
(function () {
    "use strict";

    document.querySelectorAll("[data-lookup]").forEach(function (button) {
        var form = button.form;
        var text = form.elements.namedItem(button.dataset.target);
        var status = button.parentElement.querySelector("[data-lookup-status]");
        var box = button.parentElement.querySelector("[data-instrumental]");

        button.addEventListener("click", function () {
            var data = new FormData();
            data.append("csrf", form.elements.namedItem("csrf").value);
            data.append("folder", form.elements.namedItem("folder").value);
            data.append("file", button.dataset.file);
            data.append("artist", form.elements.namedItem(button.dataset.track + ".artist").value);
            data.append("title", form.elements.namedItem(button.dataset.track + ".title").value);
            data.append("album", form.elements.namedItem("album.album").value);
            button.disabled = true;
            status.textContent = button.dataset.running;
            fetch(button.dataset.lookup, { method: "POST", body: data })
                .then(function (response) { return response.json(); })
                .then(function (found) {
                    status.textContent = found.message;
                    if (found.text === null) {
                        return;
                    }
                    var instrumental = found.text === box.dataset.instrumental;
                    if (box.checked !== instrumental) {
                        box.checked = instrumental;
                        box.dispatchEvent(new window.Event("change"));
                    }
                    if (!instrumental) {
                        text.value = found.text;
                    }
                })
                .catch(function () { status.textContent = ""; })
                .then(function () { button.disabled = false; });
        });
    });
}());

// The awaited box marks its track at once, apart from the form, and its row says so, as does Later
// once no track is left to sort out; a mark refused leaves the box as it was.
(function () {
    "use strict";

    document.querySelectorAll("[data-awaited]").forEach(function (box) {
        var form = box.form;
        var row = box.closest("tr").previousElementSibling;
        var shown = row.querySelector("[data-lyrics-toggle]");
        var later = document.querySelector("[data-later]");

        box.addEventListener("change", function () {
            var data = new FormData();
            data.append("csrf", form.elements.namedItem("csrf").value);
            data.append("folder", form.elements.namedItem("folder").value);
            data.append("file", box.dataset.file);
            data.append("awaited", box.checked ? "1" : "0");
            box.disabled = true;
            fetch(box.dataset.awaited, { method: "POST", body: data })
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error(response.statusText);
                    }
                    box.defaultChecked = box.checked;
                    row.classList.toggle("to-sort", !box.checked);
                    shown.textContent = box.checked ? box.dataset.shownAwaited : box.dataset.shownToSort;
                    if (later) {
                        var sortedOut = !document.querySelector("tr.to-sort");
                        later.querySelector("[data-label]").textContent = sortedOut ? later.dataset.sortedOut
                            : later.dataset.later;
                        later.querySelector(".icon-after").classList.toggle("hidden", !sortedOut);
                    }
                })
                .catch(function () {
                    box.checked = box.defaultChecked;
                    window.alert(box.dataset.failed);
                })
                .then(function () { box.disabled = false; });
        });
    });
}());

// A page following a pass asks every few seconds whether it ended, then goes to the page the server
// names (the album where the pass left it), else reloads.
(function () {
    "use strict";

    var POLL_MS = 2000;
    var panel = document.querySelector("[data-follow]");
    if (!panel) {
        return;
    }

    function shown(progress) {
        var duration = panel.querySelector("[data-follow-duration]");
        var details = panel.querySelector("[data-follow-details]");
        var step = panel.querySelector("[data-follow-step]");
        if (!progress || !duration) {
            return;
        }
        duration.textContent = progress.duration || "";
        details.href = progress.url;
        details.hidden = false;
        step.textContent = progress.step;
        step.hidden = !progress.step;
    }

    function poll() {
        fetch(panel.dataset.follow)
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (!data.done) {
                    shown(data.progress);
                    setTimeout(poll, POLL_MS);
                } else if (data.album) {
                    window.location.replace(data.album);
                } else {
                    window.location.reload();
                }
            })
            .catch(function () { setTimeout(poll, POLL_MS * 5); });
    }

    setTimeout(poll, POLL_MS);
}());

// The library's search runs as it is typed or changed: the results are swapped in place, asked again
// while the album list is being built, and the address follows, each condition of the advanced search
// in its short form (c=composer:empty); a field offers the operators of its kind, an operator the
// values it takes. Without script, the button searches and the form's rows are read side by side.
(function () {
    "use strict";

    var TYPING_MS = 250;
    var BUILDING_MS = 2000;
    var form = document.querySelector("[data-live-search]");
    var results = document.getElementById("results");
    if (!form || !results) {
        return;
    }
    var kinds = JSON.parse(form.dataset.kinds);
    var bare = JSON.parse(form.dataset.bare);
    var operators = JSON.parse(form.dataset.operators);
    var names = JSON.parse(form.dataset.operatorNames);
    var list = form.querySelector("[data-conditions]");
    var add = form.querySelector("[data-add-condition]");
    var timer = null;
    var wanted = null;

    function part(row, selector) {
        return row.querySelector(selector);
    }

    function shape(row) {
        var op = part(row, "[data-operator]").value;
        part(row, "[data-tag]").hidden = part(row, "[data-field]").value !== "tag";
        part(row, "[data-value]").hidden = bare.indexOf(op) !== -1;
        part(row, "[data-upto]").hidden = op !== "between";
    }

    function offer(row) {
        var select = part(row, "[data-operator]");
        var kept = select.value;
        var ops = operators[kinds[part(row, "[data-field]").value]];
        select.innerHTML = "";
        ops.forEach(function (op) {
            select.add(new window.Option(names[op], op, false, op === kept));
        });
        shape(row);
    }

    function condition(row) {
        var field = part(row, "[data-field]").value;
        var op = part(row, "[data-operator]").value;
        var value = part(row, "[data-value]").value.trim();
        var upto = row.querySelector("[name=w]").value.trim();
        if (field === "tag") {
            var tag = part(row, "[data-tag]").value.trim().toLowerCase();
            if (!tag) {
                return null;
            }
            field = "tag." + tag;
        }
        if (bare.indexOf(op) !== -1) {
            return field + ":" + op;
        }
        if (!value || (op === "between" && !upto)) {
            return null;
        }
        return field + ":" + op + ":" + value + (op === "between" ? ":" + upto : "");
    }

    function address() {
        var params = new URLSearchParams();
        var q = form.elements.namedItem("q").value.trim();
        var sort = form.elements.namedItem("sort").value;
        var view = form.elements.namedItem("view");
        var conditions = [];
        if (q) {
            params.append("q", q);
        }
        if (sort !== "artist") {
            params.append("sort", sort);
        }
        if (view && view.value !== "albums") {
            params.append("view", view.value);
        }
        list.querySelectorAll("[data-condition]").forEach(function (row) {
            var c = condition(row);
            if (c && conditions.indexOf(c) === -1) {
                conditions.push(c);
                params.append("c", c);
            }
        });
        if (conditions.length && form.elements.namedItem("match").value === "any") {
            params.append("match", "any");
        }
        var query = params.toString().replace(/%3A/gi, ":");
        return form.action + (query ? "?" + query : "");
    }

    function search() {
        var url = address();
        if (url === wanted) {
            return;
        }
        wanted = url;
        fetch(url)
            .then(function (response) { return response.text(); })
            .then(function (html) {
                var fresh = new DOMParser().parseFromString(html, "text/html").getElementById("results");
                if (fresh && url === wanted) {
                    results.innerHTML = fresh.innerHTML;
                    window.history.replaceState(null, "", url);
                    if (fresh.querySelector("[data-reload-when-built]")) {
                        setTimeout(function () { wanted = null; search(); }, BUILDING_MS);
                    }
                }
            })
            .catch(function () { wanted = null; });
    }

    function soon() {
        clearTimeout(timer);
        timer = setTimeout(search, TYPING_MS);
    }

    function now() {
        clearTimeout(timer);
        search();
    }

    function blank(row) {
        var fresh = row.cloneNode(true);
        fresh.querySelectorAll("input").forEach(function (input) { input.value = ""; });
        part(fresh, "[data-field]").value = "any";
        offer(fresh);
        part(fresh, "[data-operator]").value = "contains";
        shape(fresh);
        part(fresh, "[data-drop]").hidden = false;
        return fresh;
    }

    list.querySelectorAll("[data-condition]").forEach(function (row) {
        part(row, "[data-drop]").hidden = false;
    });
    add.hidden = false;
    add.addEventListener("click", function () {
        var rows = list.querySelectorAll("[data-condition]");
        var row = blank(rows[rows.length - 1]);
        list.appendChild(row);
        part(row, "[data-field]").focus();
    });
    list.addEventListener("click", function (event) {
        var drop = event.target.closest("[data-drop]");
        if (!drop) {
            return;
        }
        event.preventDefault();
        var row = drop.closest("[data-condition]");
        if (list.querySelectorAll("[data-condition]").length > 1) {
            row.remove();
        } else {
            list.replaceChild(blank(row), row);
        }
        now();
    });
    form.addEventListener("input", function (event) {
        if (event.target.tagName === "INPUT" && event.target.type !== "radio") {
            soon();
        }
    });
    form.addEventListener("change", function (event) {
        var row = event.target.closest("[data-condition]");
        if (row && event.target.matches("[data-field]")) {
            offer(row);
        } else if (row && event.target.matches("[data-operator]")) {
            shape(row);
        }
        if (event.target.tagName === "SELECT" || event.target.type === "radio") {
            now();
        }
    });
    form.addEventListener("submit", function (event) {
        event.preventDefault();
        now();
    });
}());

// A dedupe question's own spelling: typing it picks it, picking a listed one empties it.
(function () {
    "use strict";

    var other = document.querySelector("[data-other-choice]");
    if (!other) {
        return;
    }
    var typed = other.form.elements.other;
    typed.addEventListener("input", function () {
        other.checked = true;
        typed.required = true;
    });
    other.form.querySelectorAll("input[name=target]").forEach(function (radio) {
        radio.addEventListener("change", function () {
            typed.required = other.checked;
            if (other.checked) {
                typed.focus();
            } else {
                typed.value = "";
            }
        });
    });
}());

// Look the edition up lists what the providers know of the album, as typed in the form; taking a year
// puts it in the album's year field, to preview with the rest of the form.
(function () {
    "use strict";

    document.querySelectorAll("[data-editions]").forEach(function (button) {
        var form = button.form;
        var out = document.getElementById(button.dataset.target);
        var status = button.parentElement.querySelector("[data-editions-status]");

        button.addEventListener("click", function () {
            var data = new FormData();
            data.append("csrf", form.elements.namedItem("csrf").value);
            data.append("folder", form.elements.namedItem("folder").value);
            data.append("albumartist", form.elements.namedItem("album.albumartist").value);
            data.append("album", form.elements.namedItem("album.album").value);
            button.disabled = true;
            status.textContent = button.dataset.running;
            fetch(button.dataset.editions, { method: "POST", body: data })
                .then(function (response) { return response.ok ? response.text() : Promise.reject(); })
                .then(function (html) {
                    out.innerHTML = html;
                    out.querySelectorAll("[data-pick-year]").forEach(function (pick) {
                        pick.addEventListener("click", function () {
                            var year = form.elements.namedItem("album.year");
                            year.value = pick.dataset.pickYear;
                            year.dispatchEvent(new window.Event("input", { bubbles: true }));
                            year.focus();
                        });
                    });
                })
                .catch(function () { out.textContent = ""; })
                .then(function () {
                    status.textContent = "";
                    button.disabled = false;
                });
        });
    });
}());

// Closing the panel of a pass that ended drops it at once; the address loses the request alike.
(function () {
    "use strict";

    document.querySelectorAll(".panel-close").forEach(function (link) {
        link.addEventListener("click", function (event) {
            event.preventDefault();
            link.closest(".panel").remove();
            window.history.replaceState(window.history.state, "", link.href);
        });
    });
}());

// Files sent to the Inbox: those the import takes, picked or dropped, folders kept whole, sent one at a
// time into a batch the dashboard moves into the Inbox once every file has arrived.
(function () {
    "use strict";

    var box = document.querySelector("[data-upload]");
    if (!box) {
        return;
    }
    var dialog = box.querySelector("dialog");
    var texts = JSON.parse(box.dataset.texts);
    var base = box.dataset.upload;
    var csrf = box.dataset.csrf;
    var extensions = box.dataset.extensions.split(" ");
    var junk = box.dataset.junk.split("/");
    var maxFile = Number(box.dataset.maxFile);
    var zone = dialog.querySelector("[data-drop-zone]");
    var inputs = { files: dialog.querySelector("[data-pick-files]"), folder: dialog.querySelector("[data-pick-folder]") };
    var list = dialog.querySelector("[data-upload-items]");
    var summary = dialog.querySelector("[data-upload-summary]");
    var progress = dialog.querySelector("[data-upload-progress]");
    var status = dialog.querySelector("[data-upload-status]");
    var send = dialog.querySelector("[data-upload-send]");
    var cancel = dialog.querySelector("[data-upload-cancel]");
    var close = dialog.querySelector("[data-upload-close]");
    var MB = 1024 * 1024;
    var chosen = [];
    var skipped = 0;
    var sending = false;
    var stopping = false;
    var finished = false;
    var xhr = null;

    function format(text, values) {
        return text.replace(/\{(\w+)\}/g, function (all, key) { return key in values ? values[key] : all; });
    }

    function number(n) {
        return n.toLocaleString(document.documentElement.lang);
    }

    function taken(path) {
        var parts = path.split("/");
        var name = parts[parts.length - 1];
        var dot = name.lastIndexOf(".");
        return parts.every(function (part) { return part && part.charAt(0) !== "."; }) &&
            junk.indexOf(name) < 0 && dot > 0 && extensions.indexOf(name.slice(dot).toLowerCase()) >= 0;
    }

    function add(file, path) {
        if (!taken(path)) {
            skipped += 1;
        } else if (!chosen.some(function (c) { return c.path === path; })) {
            chosen.push({ file: file, path: path });
        }
    }

    function total() {
        return chosen.reduce(function (sum, c) { return sum + c.file.size; }, 0);
    }

    function show() {
        var groups = [];
        var counts = {};
        chosen.forEach(function (c) {
            var top = c.path.split("/")[0];
            if (!(top in counts)) {
                groups.push({ name: top, folder: c.path !== top });
                counts[top] = 0;
            }
            counts[top] += 1;
        });
        list.textContent = "";
        groups.forEach(function (group) {
            var li = document.createElement("li");
            var n = counts[group.name];
            li.textContent = !group.folder ? group.name
                : format(n === 1 ? texts.folder_one : texts.folder_many, { name: group.name, n: number(n) });
            list.appendChild(li);
        });
        var parts = [];
        if (chosen.length) {
            parts.push(format(texts.selected, { n: number(chosen.length), size: number(Math.max(1, Math.round(total() / MB))) }));
        }
        if (skipped) {
            parts.push(format(texts.skipped, { n: number(skipped) }));
        }
        summary.textContent = parts.join(" · ");
        send.disabled = !chosen.length || sending || finished;
    }

    function walk(entry, path) {
        return new Promise(function (resolve) {
            if (entry.isFile) {
                entry.file(function (file) { add(file, path); resolve(); }, function () { resolve(); });
                return;
            }
            if (!entry.isDirectory) {
                resolve();
                return;
            }
            var reader = entry.createReader();
            var entries = [];
            (function more() {
                reader.readEntries(function (some) {
                    if (some.length) {
                        entries = entries.concat(some);
                        more();
                    } else {
                        Promise.all(entries.map(function (e) { return walk(e, path + "/" + e.name); })).then(resolve);
                    }
                }, function () { resolve(); });
            }());
        });
    }

    function reset() {
        chosen = [];
        skipped = 0;
        stopping = false;
        finished = false;
        progress.hidden = true;
        progress.value = 0;
        status.textContent = "";
        send.hidden = cancel.hidden = false;
        close.hidden = true;
        show();
    }

    function form(values) {
        var data = new FormData();
        data.append("csrf", csrf);
        Object.keys(values).forEach(function (key) { data.append(key, values[key]); });
        return data;
    }

    function reason(text) {
        try {
            return JSON.parse(text).error || texts.unanswered;
        } catch (e) {
            return texts.unanswered;
        }
    }

    function post(url, values) {
        return fetch(url, { method: "POST", body: form(values) })
            .catch(function () { return Promise.reject(texts.unanswered); })
            .then(function (response) {
                return response.text().then(function (text) {
                    if (!response.ok) {
                        return Promise.reject(reason(text));
                    }
                    return text ? JSON.parse(text) : null;
                });
            });
    }

    function sendFile(batch, c, before, size, index) {
        return new Promise(function (resolve, reject) {
            if (stopping) {
                reject(null);
                return;
            }
            xhr = new XMLHttpRequest();
            xhr.open("POST", base + "/" + batch + "?path=" + encodeURIComponent(c.path));
            xhr.setRequestHeader("X-CSRF-Token", csrf);
            xhr.setRequestHeader("Content-Type", "application/octet-stream");
            xhr.upload.addEventListener("progress", function (event) {
                progress.value = size ? (before + event.loaded) / size : 1;
            });
            xhr.addEventListener("load", function () {
                if (xhr.status === 204) {
                    resolve();
                } else {
                    reject(reason(xhr.responseText));
                }
            });
            xhr.addEventListener("error", function () { reject(texts.unanswered); });
            xhr.addEventListener("abort", function () { reject(null); });
            status.textContent = format(texts.sending, { done: number(index + 1), total: number(chosen.length), name: c.path });
            xhr.send(c.file);
        });
    }

    function start() {
        var big = chosen.filter(function (c) { return c.file.size > maxFile; })[0];
        if (big) {
            status.textContent = format(texts.too_large, { name: big.path, gb: number(maxFile / 1024 / MB) });
            return;
        }
        var size = total();
        var batch = null;
        sending = true;
        stopping = false;
        progress.hidden = false;
        progress.value = 0;
        show();
        post(base, { size: size })
            .then(function (answer) {
                batch = answer.id;
                var before = 0;
                return chosen.reduce(function (previous, c, index) {
                    return previous.then(function () {
                        var done = sendFile(batch, c, before, size, index);
                        before += c.file.size;
                        return done;
                    });
                }, Promise.resolve());
            })
            .then(function () { return post(base + "/" + batch + "/done", {}); })
            .then(function (answer) {
                finished = true;
                progress.value = 1;
                status.textContent = answer.message;
                send.hidden = cancel.hidden = true;
                close.hidden = false;
                close.focus();
            })
            .catch(function (why) {
                if (batch) {
                    post(base + "/" + batch + "/cancel", {}).catch(function () { return null; });
                }
                progress.hidden = true;
                status.textContent = why === null ? texts.cancelled : format(texts.failed, { reason: why });
            })
            .then(function () {
                sending = false;
                xhr = null;
                show();
            });
    }

    function stop() {
        stopping = true;
        if (xhr) {
            xhr.abort();
        }
    }

    box.querySelector("[data-upload-open]").addEventListener("click", function () {
        reset();
        dialog.showModal();
    });
    dialog.querySelectorAll("[data-picks]").forEach(function (button) {
        button.addEventListener("click", function () {
            if (!sending && !finished) {
                inputs[button.dataset.picks].click();
            }
        });
    });
    Object.keys(inputs).forEach(function (kind) {
        inputs[kind].addEventListener("change", function () {
            Array.prototype.forEach.call(inputs[kind].files, function (file) {
                add(file, file.webkitRelativePath || file.name);
            });
            inputs[kind].value = "";
            show();
        });
    });
    dialog.addEventListener("dragover", function (event) {
        event.preventDefault();
        zone.classList.add("over");
    });
    dialog.addEventListener("dragleave", function (event) {
        if (!dialog.contains(event.relatedTarget)) {
            zone.classList.remove("over");
        }
    });
    dialog.addEventListener("drop", function (event) {
        event.preventDefault();
        zone.classList.remove("over");
        if (sending || finished) {
            return;
        }
        var entries = Array.prototype.map.call(event.dataTransfer.items, function (item) {
            return item.webkitGetAsEntry ? item.webkitGetAsEntry() : null;
        });
        if (entries.length && entries.every(Boolean)) {
            Promise.all(entries.map(function (entry) { return walk(entry, entry.name); })).then(show);
        } else {
            Array.prototype.forEach.call(event.dataTransfer.files, function (file) { add(file, file.name); });
            show();
        }
    });
    send.addEventListener("click", start);
    cancel.addEventListener("click", function () {
        if (sending) {
            stop();
        } else {
            dialog.close();
        }
    });
    dialog.addEventListener("cancel", function (event) {
        if (sending) {
            event.preventDefault();
            stop();
        }
    });
    close.addEventListener("click", function () { dialog.close(); });
    dialog.addEventListener("close", function () {
        if (finished) {
            window.location.reload();
        }
    });
    window.addEventListener("beforeunload", function (event) {
        if (sending) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
}());
