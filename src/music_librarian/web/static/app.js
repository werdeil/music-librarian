// A filter applied as soon as it changes.
(function () {
    "use strict";

    document.querySelectorAll("[data-autosubmit]").forEach(function (input) {
        input.addEventListener("change", function () {
            input.form.submit();
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

// A page showing lists still being built reloads once they are, unless something was typed,
// ticked or unfolded meanwhile.
(function () {
    "use strict";

    var POLL_MS = 2000;
    var marker = document.querySelector("[data-reload-when-built]");
    if (!marker) {
        return;
    }
    var url = marker.dataset.reloadWhenBuilt;
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
