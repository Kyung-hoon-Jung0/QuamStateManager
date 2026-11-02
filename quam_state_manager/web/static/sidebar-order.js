/* Sidebar ordering: DOM nodes move intact so navigation and collapse state survive. */
(function () {
    'use strict';
    const KEY = 'quam_sidebar_order_v1';
    if (!document.getElementById('sidebar')) document.documentElement.classList.add('ui-reorder-pending');
    const defaults = new WeakMap();
    let drag = null;
    let suppressClick = null;
    function items(group) {
        return Array.from(group.children).filter(el => el.matches('li[data-nav-key]'));
    }
    function read() {
        try {
            const value = JSON.parse(localStorage.getItem(KEY));
            return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
        } catch (_) { return {}; }
    }
    function groups() { return document.querySelectorAll('#sidebar [data-nav-group]'); }
    function apply() {
        const saved = read();
        groups().forEach(group => {
            const rows = items(group);
            rows.forEach(row => row.classList.add('ui-reorder-item'));
            if (!defaults.has(group) || rows.some(row => !defaults.get(group).includes(row))) {
                defaults.set(group, rows.slice());
            }
            const order = saved[group.dataset.navGroup];
            if (!Array.isArray(order) || order.some(key => typeof key !== 'string') || new Set(order).size !== order.length) return;
            const known = order.map(key => rows.find(row => row.dataset.navKey === key)).filter(Boolean);
            const members = new Set(known);
            const slots = rows.map(row => members.has(row) ? known.shift() : row);
            // Replace only movable slots; notes and other controls retain their positions.
            const markers = rows.map(row => {
                const marker = document.createComment('nav order');
                row.replaceWith(marker);
                return marker;
            });
            markers.forEach((marker, i) => marker.replaceWith(slots[i]));
        });
        document.documentElement.classList.remove('ui-reorder-pending');
    }
    function restore(group, rows) {
        const markers = items(group).map(row => {
            const marker = document.createComment('nav order');
            row.replaceWith(marker);
            return marker;
        });
        markers.forEach((marker, i) => marker.replaceWith(rows[i]));
    }
    function persist(group) {
        const saved = read();
        const rows = items(group);
        const original = defaults.get(group) || rows;
        if (rows.every((row, i) => row === original[i])) delete saved[group.dataset.navGroup];
        else saved[group.dataset.navGroup] = rows.map(row => row.dataset.navKey);
        try {
            if (Object.keys(saved).length) localStorage.setItem(KEY, JSON.stringify(saved));
            else localStorage.removeItem(KEY);
        } catch (_) { /* Storage may be unavailable. */ }
    }
    function rowFor(target) {
        const row = target.closest('#sidebar li[data-nav-key]');
        return row && row.parentElement.matches('[data-nav-group]') ? row : null;
    }
    function clearIndicator() {
        document.querySelectorAll('.ui-reorder-before, .ui-reorder-after').forEach(el => el.classList.remove('ui-reorder-before', 'ui-reorder-after'));
    }
    function finish(cancel) {
        if (!drag) return;
        if (drag.active) {
            suppressClick = drag.row;
            if (!cancel && drag.target) {
                drag.group.insertBefore(drag.row, drag.after ? drag.target.nextSibling : drag.target);
                persist(drag.group);
            }
        }
        if (drag.row.hasPointerCapture && drag.row.hasPointerCapture(drag.id)) drag.row.releasePointerCapture(drag.id);
        drag.row.classList.remove('ui-reorder-dragging');
        clearIndicator();
        drag = null;
    }
    document.addEventListener('pointerdown', event => {
        suppressClick = null;
        if (event.button !== 0 || event.isPrimary === false || event.target.closest('.nav-sub-toggle, .nav-print, input, select, textarea')) return;
        const row = rowFor(event.target);
        if (row) drag = { row, group: row.parentElement, x: event.clientX, y: event.clientY, id: event.pointerId, active: false };
    });
    document.addEventListener('pointermove', event => {
        if (!drag || event.pointerId !== drag.id) return;
        if (!drag.active && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 4) return;
        if (!drag.active && drag.row.setPointerCapture && event.pointerId !== undefined) {
            drag.row.setPointerCapture(event.pointerId);
        }
        drag.active = true;
        drag.row.classList.add('ui-reorder-dragging');
        event.preventDefault();
        clearIndicator();
        drag.target = null;
        const hit = document.elementFromPoint ? document.elementFromPoint(event.clientX, event.clientY) : event.target;
        // A point over an expanded sub-menu names the item in THIS group that holds it.
        let target = hit && hit.closest ? hit.closest('li[data-nav-key]') : null;
        while (target && target.parentElement !== drag.group) {
            target = target.parentElement && target.parentElement.closest('li[data-nav-key]');
        }
        if (!target || target === drag.row) return;
        const rect = target.getBoundingClientRect();
        drag.target = target;
        drag.after = event.clientY >= rect.top + rect.height / 2;
        target.classList.add(drag.after ? 'ui-reorder-after' : 'ui-reorder-before');
    }, { passive: false });
    document.addEventListener('pointerup', event => { if (drag && event.pointerId === drag.id) finish(false); });
    document.addEventListener('pointercancel', () => finish(true));
    window.addEventListener('blur', () => finish(true));
    document.addEventListener('dragstart', event => { if (rowFor(event.target)) event.preventDefault(); });
    document.addEventListener('click', event => {
        if (suppressClick) { event.preventDefault(); event.stopImmediatePropagation(); suppressClick = null; }
        if (event.target.closest('#reset-menu-order')) {
            finish(true);
            groups().forEach(group => { if (defaults.has(group)) restore(group, defaults.get(group)); });
            try { localStorage.removeItem(KEY); } catch (_) { /* Storage may be unavailable. */ }
        }
    }, true);
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && drag) { event.preventDefault(); finish(true); return; }
        if (!event.altKey || !['ArrowUp', 'ArrowDown'].includes(event.key) || !event.target.matches('a, button[type="submit"]')) return;
        const row = rowFor(event.target);
        if (!row) return;
        const rows = items(row.parentElement);
        const index = rows.indexOf(row);
        const other = rows[index + (event.key === 'ArrowUp' ? -1 : 1)];
        event.preventDefault();
        if (!other) return;
        row.parentElement.insertBefore(row, event.key === 'ArrowUp' ? other : other.nextSibling);
        event.target.focus();
        persist(row.parentElement);
    });
    // The core script loads in the head. Observe parsing until the sidebar is
    // complete; mutation callbacks run before rendering, avoiding an order flash.
    if (document.getElementById('sidebar')) apply();
    else {
        const observer = new MutationObserver(() => {
            const sidebar = document.getElementById('sidebar');
            if (sidebar && sidebar.nextElementSibling) {
                observer.disconnect();
                apply();
            }
        });
        observer.observe(document.documentElement, { childList: true, subtree: true });
        document.addEventListener('DOMContentLoaded', () => { observer.disconnect(); apply(); }, { once: true });
    }
    document.addEventListener('htmx:afterSwap', apply);
})();
