/* KubeDDoS Attacks — Shared JavaScript */
(function () {
    'use strict';

    const socket = io();

    socket.on('connect', () => console.log('[KubeDDoS-Attacks] WebSocket connected'));
    socket.on('disconnect', () => console.log('[KubeDDoS-Attacks] WebSocket disconnected'));

    // Update active attacks badge
    socket.on('attack_status_update', function (data) {
        const badge = document.getElementById('active-attacks-badge');
        if (badge && data.attacks) {
            const running = data.attacks.filter(a => a.running).length;
            badge.textContent = `${running} active`;
            badge.className = running > 0 ? 'badge bg-danger' : 'badge bg-secondary';
        }
    });

    window.kubeddos = window.kubeddos || {};

    window.kubeddos.fetchJSON = async function (url) {
        try {
            const resp = await fetch(url);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            return await resp.json();
        } catch (err) {
            console.error(`[KubeDDoS-Attacks] Fetch error: ${url}`, err);
            showToast('error', `Failed to load ${url}: ${err.message}`);
            return null;
        }
    };

    window.kubeddos.formatTime = function (ts) {
        if (!ts) return '—';
        return new Date(ts).toLocaleString();
    };

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

    // Socket event handlers
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

    // Toast notifications
    let toastContainer = null;
    function showToast(type, message) {
        if (!toastContainer) {
            toastContainer = document.createElement('div');
            toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
            document.body.appendChild(toastContainer);
        }
        const colorMap = { error: 'danger', success: 'success', info: 'info', warning: 'warning' };
        const color = colorMap[type] || 'secondary';
        const el = document.createElement('div');
        el.className = `toast align-items-center text-bg-${color} border-0`;
        el.setAttribute('role', 'alert');
        el.innerHTML = `<div class="d-flex"><div class="toast-body">${message}</div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>`;
        toastContainer.appendChild(el);
        const toast = new bootstrap.Toast(el, { delay: 5000 });
        toast.show();
        el.addEventListener('hidden.bs.toast', () => el.remove());
    }
    window.kubeddos.showToast = showToast;

    // Chart.js defaults
    if (typeof Chart !== 'undefined') {
        Chart.defaults.color = '#adb5bd';
        Chart.defaults.borderColor = 'rgba(255,255,255,0.1)';
    }
})();
