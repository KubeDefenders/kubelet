/* KubeDDoS Dashboard — Shared JavaScript */

(function () {
    'use strict';

    // ── Socket.IO Connection ──────────────────────────────────────
    const socket = io();

    socket.on('connect', function () {
        console.log('[KubeDDoS] WebSocket connected');
        updateHealthBadge('connected');
    });

    socket.on('disconnect', function () {
        console.log('[KubeDDoS] WebSocket disconnected');
        updateHealthBadge('disconnected');
    });

    // ── Health Badge ──────────────────────────────────────────────
    function updateHealthBadge(state) {
        const badge = document.getElementById('health-badge');
        if (!badge) return;

        if (state === 'connected') {
            badge.className = 'badge bg-success ms-2';
            badge.textContent = 'Connected';
        } else if (state === 'disconnected') {
            badge.className = 'badge bg-danger ms-2';
            badge.textContent = 'Disconnected';
        } else if (state === 'healthy') {
            badge.className = 'badge bg-success ms-2';
            badge.textContent = 'Healthy';
        } else if (state === 'degraded') {
            badge.className = 'badge bg-warning ms-2';
            badge.textContent = 'Degraded';
        } else {
            badge.className = 'badge bg-secondary ms-2';
            badge.textContent = state;
        }
    }

    // Periodic health check
    function checkHealth() {
        fetch('/api/health')
            .then(r => r.json())
            .then(data => {
                if (data.status === 'healthy') {
                    updateHealthBadge('healthy');
                } else {
                    updateHealthBadge('degraded');
                }
            })
            .catch(() => updateHealthBadge('disconnected'));
    }

    setInterval(checkHealth, 30000);
    checkHealth();

    // ── Utility Functions ─────────────────────────────────────────

    /**
     * Fetch JSON from an API endpoint.
     * Displays a toast on error.
     */
    window.kubeddos = window.kubeddos || {};

    window.kubeddos.fetchJSON = async function (url) {
        try {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            return await resp.json();
        } catch (err) {
            console.error(`[KubeDDoS] Fetch error: ${url}`, err);
            showToast('error', `Failed to load ${url}: ${err.message}`);
            return null;
        }
    };

    /**
     * Format a Kubernetes timestamp to locale string.
     */
    window.kubeddos.formatTime = function (ts) {
        if (!ts) return '—';
        const d = new Date(ts);
        return d.toLocaleString();
    };

    /**
     * Format a duration in seconds to "Xm Ys" or "Xh Ym".
     */
    window.kubeddos.formatDuration = function (seconds) {
        if (seconds == null || seconds < 0) return '—';
        if (seconds < 60) return `${Math.round(seconds)}s`;
        if (seconds < 3600) {
            const m = Math.floor(seconds / 60);
            const s = Math.round(seconds % 60);
            return `${m}m ${s}s`;
        }
        const h = Math.floor(seconds / 3600);
        const m = Math.round((seconds % 3600) / 60);
        return `${h}h ${m}m`;
    };

    /**
     * Return a Bootstrap color class for a severity level.
     */
    window.kubeddos.severityColor = function (severity) {
        const map = {
            'CRITICAL': 'danger',
            'HIGH': 'warning',
            'MEDIUM': 'info',
            'LOW': 'success'
        };
        return map[severity] || 'secondary';
    };

    /**
     * Return a Bootstrap color class for a mitigation phase.
     */
    window.kubeddos.phaseColor = function (phase) {
        const map = {
            'Pending': 'secondary',
            'Active': 'primary',
            'Reverting': 'warning',
            'Completed': 'success',
            'Failed': 'danger'
        };
        return map[phase] || 'secondary';
    };

    /**
     * Return a badge class for a pod phase.
     */
    window.kubeddos.podPhaseColor = function (phase) {
        const map = {
            'Running': 'success',
            'Succeeded': 'info',
            'Pending': 'warning',
            'Failed': 'danger',
            'Unknown': 'secondary'
        };
        return map[phase] || 'secondary';
    };

    /**
     * Determine invariant status badge.
     */
    window.kubeddos.invariantBadge = function (value, threshold, inverted) {
        if (value == null) return '<span class="badge bg-secondary">N/A</span>';
        const exceeded = inverted ? value < threshold : value > threshold;
        if (exceeded) {
            return `<span class="badge bg-danger">VIOLATED</span>`;
        }
        return `<span class="badge bg-success">OK</span>`;
    };

    // ── Toast Notifications ───────────────────────────────────────
    let toastContainer = null;

    function ensureToastContainer() {
        if (toastContainer) return toastContainer;
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
        return toastContainer;
    }

    function showToast(type, message) {
        const container = ensureToastContainer();
        const colorMap = { error: 'danger', success: 'success', info: 'info', warning: 'warning' };
        const color = colorMap[type] || 'secondary';

        const toastEl = document.createElement('div');
        toastEl.className = `toast align-items-center text-bg-${color} border-0`;
        toastEl.setAttribute('role', 'alert');
        toastEl.innerHTML = `
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        `;
        container.appendChild(toastEl);

        const toast = new bootstrap.Toast(toastEl, { delay: 5000 });
        toast.show();

        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    }

    window.kubeddos.showToast = showToast;

    // ── Socket.IO Event Handlers ──────────────────────────────────
    // Pages can register handlers via kubeddos.on()
    const handlers = {};

    window.kubeddos.on = function (event, callback) {
        if (!handlers[event]) {
            handlers[event] = [];
            socket.on(event, function (data) {
                handlers[event].forEach(fn => fn(data));
            });
        }
        handlers[event].push(callback);
    };

    // ── Chart.js Default Config ───────────────────────────────────
    if (typeof Chart !== 'undefined') {
        Chart.defaults.color = '#adb5bd';
        Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.1)';
        Chart.defaults.font.family = "'Segoe UI', system-ui, -apple-system, sans-serif";
    }

})();
