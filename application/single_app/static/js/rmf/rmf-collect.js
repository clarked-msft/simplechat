/**
 * rmf-collect.js — Collect from Azure UI for the RMF evidence tab.
 *
 * Looks for [data-rmf-collect-form] in the DOM and wires up the collect flow:
 *   1. User fills in subscription ID + optional resource group, toggles agentic
 *   2. POST /api/rmf/workspace/evidence/collect  → job_id
 *   3. Poll GET .../collect/jobs/<job_id> every 3 s
 *   4. Append log lines to the log panel; show final status
 *   5. On success, dispatch "rmf:evidence-refresh" so the evidence table reloads
 */

(function () {
  "use strict";

  const POLL_INTERVAL_MS = 3000;
  const TERMINAL_STATUSES = new Set(["succeeded", "failed"]);

  function init() {
    const form = document.querySelector("[data-rmf-collect-form]");
    if (!form) return;

    const submitBtn = form.querySelector("[data-rmf-collect-submit]");
    const subscriptionInput = form.querySelector("[data-rmf-collect-subscription]");
    const resourceGroupInput = form.querySelector("[data-rmf-collect-resource-group]");
    const agenticToggle = form.querySelector("[data-rmf-collect-agentic]");
    const statusPanel = form.querySelector("[data-rmf-collect-status]");
    const logPanel = form.querySelector("[data-rmf-collect-log]");
    const statusBadge = form.querySelector("[data-rmf-collect-status-badge]");

    let pollTimer = null;

    function setRunning(running) {
      submitBtn.disabled = running;
      subscriptionInput.disabled = running;
      if (resourceGroupInput) resourceGroupInput.disabled = running;
      if (agenticToggle) agenticToggle.disabled = running;
      submitBtn.textContent = running ? "Collecting…" : "Collect";
    }

    function showStatus(visible) {
      statusPanel.hidden = !visible;
    }

    function appendLog(lines) {
      if (!logPanel) return;
      lines.forEach(function (line) {
        const el = document.createElement("div");
        el.textContent = line;
        logPanel.appendChild(el);
        logPanel.scrollTop = logPanel.scrollHeight;
      });
    }

    function setBadge(status) {
      if (!statusBadge) return;
      const map = {
        queued: ["bg-secondary", "Queued"],
        running: ["bg-primary", "Running"],
        succeeded: ["bg-success", "Succeeded"],
        failed: ["bg-danger", "Failed"],
      };
      const [cls, label] = map[status] || ["bg-secondary", status];
      statusBadge.className = "badge " + cls;
      statusBadge.textContent = label;
    }

    function poll(jobId, seenLogCount) {
      fetch("/api/rmf/workspace/evidence/collect/jobs/" + encodeURIComponent(jobId))
        .then(function (r) { return r.json(); })
        .then(function (job) {
          const log = job.log || [];
          if (log.length > seenLogCount) {
            appendLog(log.slice(seenLogCount));
          }
          setBadge(job.status);

          if (TERMINAL_STATUSES.has(job.status)) {
            clearTimeout(pollTimer);
            setRunning(false);
            if (job.status === "succeeded") {
              document.dispatchEvent(new CustomEvent("rmf:evidence-refresh"));
            } else if (job.error) {
              appendLog(["Error: " + job.error]);
            }
          } else {
            pollTimer = setTimeout(function () {
              poll(jobId, log.length);
            }, POLL_INTERVAL_MS);
          }
        })
        .catch(function (err) {
          appendLog(["Poll error: " + err.message]);
          setRunning(false);
        });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();

      const subscriptionId = (subscriptionInput.value || "").trim();
      if (!subscriptionId) {
        subscriptionInput.focus();
        return;
      }

      const resourceGroup = resourceGroupInput
        ? (resourceGroupInput.value || "").trim() || null
        : null;
      const agentic = agenticToggle ? agenticToggle.checked : false;

      // Reset log
      if (logPanel) logPanel.innerHTML = "";
      showStatus(true);
      setBadge("queued");
      setRunning(true);

      fetch("/api/rmf/workspace/evidence/collect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          subscription_id: subscriptionId,
          resource_group: resourceGroup,
          agentic: agentic,
        }),
      })
        .then(function (r) {
          if (!r.ok) {
            return r.json().then(function (body) {
              throw new Error(body.error || r.statusText);
            });
          }
          return r.json();
        })
        .then(function (job) {
          setBadge(job.status);
          appendLog(["Job started: " + job.id]);
          poll(job.id, 0);
        })
        .catch(function (err) {
          showStatus(true);
          appendLog(["Failed to start collection: " + err.message]);
          setBadge("failed");
          setRunning(false);
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
