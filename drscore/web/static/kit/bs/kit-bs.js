// Site kit 1.7.0 - kit-bs: the behaviour of the app frame on Bootstrap 5.3 / Tabler 1.x pages (the "Shell Kit", from
// petweb 4.38.0). Load it in <head> after ../kit.js, WITHOUT defer: it sets the folded menu before the page paints, then
// wires the page once it is parsed (Bootstrap's JS, at the end of <body>, has run by then). No framework, no text of
// its own (every word comes from the page). Everything is opt-in by data attributes (README "Bootstrap and Tabler apps"):
//   [data-sk-fold]          a button that folds the menu to its icons (wide screens), remembered
//   [data-theme-toggle]     the kit's light / dark switch (kit.js); [data-sk-theme-toggle] (before 1.3.0) does the same
//   [data-sk-whats-new]     a modal (with an id) shown once per user and version, filled from data-sk-source
//   [data-sk-confirm]       on a form or a button: asks before it goes on
//   table.sk-table-stack    each cell gets the label of its column (cards on a phone)
// Choices are stored under "<html data-sk-app>.sk.*" in localStorage (the theme: the kit's "theme"); a browser that
// blocks it still works.
(() => {
    'use strict';
    const root = document.documentElement;
    const app = root.getAttribute('data-sk-app') || 'app';
    const store = (key, value) => {
        try {
            if (value === undefined) return localStorage.getItem(app + '.sk.' + key);
            if (value === null) localStorage.removeItem(app + '.sk.' + key);
            else localStorage.setItem(app + '.sk.' + key, value);
        } catch (e) { return null; }
        return null;
    };

    // Before the page paints: the menu folded (Tabler's folded-hover). A theme chosen with the Shell Kit before 1.3.0
    // becomes the kit's choice once.
    if (store('folded') === '1') root.setAttribute('data-bs-sidebar', 'folded-hover');
    try {
        const old = localStorage.getItem(app + '.sk.theme');
        if (old === 'dark' || old === 'light') {
            if (!localStorage.getItem('theme')) { localStorage.setItem('theme', old); root.setAttribute('data-theme', old); }
            localStorage.removeItem(app + '.sk.theme');
        }
    } catch (e) { /* not kept */ }
    root.removeAttribute('data-bs-theme');   // the kit's data-theme rules light and dark (Tabler 1.6 reads it)

    const init = () => {
        // The menu folded to its icons, on wide screens.
        document.querySelectorAll('[data-sk-fold]').forEach((button) => {
            const show = () => button.setAttribute('aria-pressed', String(root.getAttribute('data-bs-sidebar') === 'folded-hover'));
            show();
            button.addEventListener('click', () => {
                const folded = root.getAttribute('data-bs-sidebar') === 'folded-hover';
                if (folded) root.removeAttribute('data-bs-sidebar'); else root.setAttribute('data-bs-sidebar', 'folded-hover');
                store('folded', folded ? null : '1');
                show();
            });
        });

        // The old switch name does what the kit's does.
        document.querySelectorAll('[data-sk-theme-toggle]:not([data-theme-toggle])').forEach((button) => button.addEventListener('click', () => {
            const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            root.setAttribute('data-theme', next);
            try { localStorage.setItem('theme', next); } catch (e) { /* not kept */ }
        }));

        // On a phone or a tablet in portrait the menu is a drawer: following a link closes it.
        const drawer = document.querySelector('[data-sk-sidebar] .offcanvas-lg');
        if (drawer) {
            drawer.addEventListener('click', (event) => {
                const link = event.target.closest('a[href]');
                if (!link || link.getAttribute('data-bs-toggle') || matchMedia('(min-width: 992px)').matches) return;
                const close = drawer.querySelector('[data-bs-dismiss=offcanvas]');
                if (close) close.click();
            });
        }

        // Ask before a form or a button goes on.
        document.addEventListener('submit', (event) => {
            const text = event.target.getAttribute && event.target.getAttribute('data-sk-confirm');
            if (text && !confirm(text)) event.preventDefault();
        }, true);
        document.addEventListener('click', (event) => {
            const el = event.target.closest && event.target.closest('a[data-sk-confirm], button[data-sk-confirm]:not([type=submit])');
            if (el && !confirm(el.getAttribute('data-sk-confirm'))) event.preventDefault();
        }, true);

        // Tables that become cards on a phone: each cell carries its column's name.
        document.querySelectorAll('table.sk-table-stack').forEach((table) => {
            const heads = [...table.querySelectorAll('thead th')].map((th) => th.textContent.trim());
            table.querySelectorAll('tbody tr').forEach((tr) => [...tr.children].forEach((td, i) => {
                if (heads[i] && !td.hasAttribute('data-label')) td.setAttribute('data-label', heads[i]);
            }));
        });

        // "What's new": once per user and version. The source answers
        //   GET <data-sk-source>?since=<version last seen> ->
        //   {"releases": [{"version": "1.7.0", "date": "2026-11-09", "changes": [{"html": "...", "details": ["..."]}]}]}
        // (html already escaped by the server). Without a version seen before, it shows the current one only.
        const box = document.querySelector('[data-sk-whats-new]');
        if (box && window.fetch) {
            const version = box.getAttribute('data-sk-version');
            const key = 'seen.' + (box.getAttribute('data-sk-user') || '-');
            const seen = store(key);
            if (version && seen !== version) {
                fetch(box.getAttribute('data-sk-source') + (seen ? '?since=' + encodeURIComponent(seen) : ''), { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
                    .then((answer) => (answer.ok ? answer.json() : null))
                    .then((data) => {
                        if (!data || !data.releases || !data.releases.length) { store(key, version); return; }
                        box.querySelector('[data-sk-whats-new-body]').innerHTML = data.releases.map((release) =>
                            '<h3>' + escapeText(release.version) + ' <small class="text-secondary fw-normal">' + escapeText(release.date) + '</small></h3><ul>' +
                            release.changes.map((change) => '<li>' + change.html + (change.details && change.details.length ? '<ul><li>' + change.details.join('</li><li>') + '</li></ul>' : '') + '</li>').join('') +
                            '</ul>').join('');
                        if (bootstrapCall('modal', box)) store(key, version);
                    })
                    .catch(() => { /* shown next time */ });
            }
        }
    };

    // Opens a Bootstrap component through its data attributes, so the kit works whether or not the build exposes
    // window.bootstrap (Tabler's does not). The element needs an id.
    const bootstrapCall = (kind, el) => {
        if (!el.id) return false;
        const button = document.createElement('button');
        button.type = 'button';
        button.hidden = true;
        button.setAttribute('data-bs-toggle', kind);
        button.setAttribute('data-bs-target', '#' + el.id);
        document.body.appendChild(button);
        button.click();
        button.remove();
        return true;
    };
    const escapeText = (text) => {
        const div = document.createElement('div');
        div.textContent = text == null ? '' : String(text);
        return div.innerHTML;
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
