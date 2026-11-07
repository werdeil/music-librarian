// A filter applied as soon as it changes.
(function () {
    "use strict";

    document.querySelectorAll("[data-autosubmit]").forEach(function (input) {
        input.addEventListener("change", function () {
            input.form.submit();
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

// The instrumental box puts the mark in the lyrics and locks them; unticked, the text it replaced comes back.
(function () {
    "use strict";

    document.querySelectorAll("[data-instrumental]").forEach(function (box) {
        var text = box.form.elements.namedItem("lyrics");
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
