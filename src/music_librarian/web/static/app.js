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
            table.classList.add("discs");
            button.hidden = true;
        });
    });
}());

// On a phone, a track's card folds to its number and title; one holding a repeated value starts open.
(function () {
    "use strict";

    document.querySelectorAll("[data-track-more]").forEach(function (button) {
        var row = button.closest("tr");
        function show(open) {
            row.classList.toggle("folded", !open);
            button.setAttribute("aria-expanded", open ? "true" : "false");
        }
        button.hidden = false;
        show(row.querySelector("input.repeated") !== null);
        button.addEventListener("click", function () {
            show(row.classList.contains("folded"));
        });
    });
}());

// A pass card opens its options in a dialog, each warning shown while its box makes the pass long, and
// a box under another unticked and disabled while that one is; without script, the card submits the
// options as ticked by default.
(function () {
    "use strict";

    document.querySelectorAll("[data-opens-dialog]").forEach(function (opener) {
        var form = opener.form;
        var dialog = form.querySelector("dialog");
        var warnings = dialog.querySelectorAll("[data-long]");
        var subs = dialog.querySelectorAll("[data-under]");

        function update() {
            subs.forEach(function (sub) {
                sub.disabled = !form.elements.namedItem(sub.dataset.under).checked;
                if (sub.disabled) {
                    sub.checked = false;
                }
            });
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

// Look up online puts what the providers have for the track in its lyrics box, to preview with the
// rest of the form; the instrumental box follows.
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

// A page following a pass asks every few seconds whether it ended, then goes to the page the server
// names (the album where the pass left it), else reloads.
(function () {
    "use strict";

    var POLL_MS = 2000;
    var panel = document.querySelector("[data-follow]");
    if (!panel) {
        return;
    }

    function poll() {
        fetch(panel.dataset.follow)
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (!data.done) {
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
