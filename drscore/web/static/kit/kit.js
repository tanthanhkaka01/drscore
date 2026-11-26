// Site kit 1.7.0: the shared behaviour of a family of static sites (and, from 1.2.0, of app pages: the k-app frame).
// Load it in <head> WITHOUT defer or async: it sets the light/dark theme before the page paints,
// then wires the page up once the document is parsed. It makes no network request of its own; the
// only one it can cause is a youtube-nocookie.com player, created when a visitor presses a video's
// poster (data-video). The theme choice stays in this browser's localStorage under "theme".
(() => {
    'use strict';
    const root = document.documentElement;
    root.classList.add('k-js');

    const KEY = 'theme';
    const media = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
    const stored = () => {
        try { const value = localStorage.getItem(KEY); return value === 'light' || value === 'dark' ? value : null; } catch (e) { return null; }
    };
    const system = () => (media && media.matches ? 'dark' : 'light');
    const effective = () => root.getAttribute('data-theme') || system();
    // The visitor's choice if they made one, else the system's; the CSS alone follows the system
    // when scripts do not run. A page with one theme only says so on <html data-theme-lock="light|dark"> (1.3.0: an
    // app whose own styles are not made for the other one); its switches are hidden.
    const LOCK = ['light', 'dark'].includes(root.getAttribute('data-theme-lock')) ? root.getAttribute('data-theme-lock') : null;
    root.setAttribute('data-theme', LOCK || stored() || system());
    // The kit's own version (independent of the page's): on <html data-kit-version>, and written into every element
    // marked data-k-kit-version (e.g. the app frame's menu foot), so a page always shows which kit it runs on.
    const KIT_VERSION = '1.7.0';
    root.setAttribute('data-kit-version', KIT_VERSION);
    // The app frame's side menu folded to its icons (k-app, 1.2.0): set before the page paints.
    try { if (localStorage.getItem('k-side') === 'folded') root.setAttribute('data-k-side', 'folded'); } catch (e) { /* not kept */ }

    // The icon sprite sits next to this file: kit/icons/sprite.svg.
    const script = document.currentScript;
    const SPRITE = script ? new URL('icons/sprite.svg', script.src).pathname : '/kit/icons/sprite.svg';
    const SVG = 'http://www.w3.org/2000/svg';
    const icon = (name) => {
        const svg = document.createElementNS(SVG, 'svg');
        svg.setAttribute('class', 'k-icon');
        svg.setAttribute('aria-hidden', 'true');
        const use = document.createElementNS(SVG, 'use');
        use.setAttribute('href', SPRITE + '#' + name);
        svg.append(use);
        return svg;
    };

    const init = () => {
        document.querySelectorAll('[data-k-kit-version]').forEach((el) => { el.textContent = 'kit ' + KIT_VERSION; });
        // Light / dark switch.
        const toggles = [...document.querySelectorAll('[data-theme-toggle]')];
        const showTheme = () => {
            const dark = effective() === 'dark';
            toggles.forEach((button) => {
                button.setAttribute('aria-pressed', String(dark));
                button.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
            });
        };
        toggles.forEach((button) => button.addEventListener('click', () => {
            const next = effective() === 'dark' ? 'light' : 'dark';
            root.setAttribute('data-theme', next);
            try { localStorage.setItem(KEY, next); } catch (e) { /* private window: not kept */ }
            showTheme();
        }));
        if (media && media.addEventListener) media.addEventListener('change', () => { if (!stored() && !LOCK) { root.setAttribute('data-theme', system()); showTheme(); } });
        showTheme();

        // Menus are <details>, so they open without scripts; here they also close on a click
        // outside, on Escape, and once a link inside is followed. One open at a time.
        const menus = [...document.querySelectorAll('details.k-menu, details.k-dropdown')];
        const closeAll = (except) => menus.forEach((menu) => { if (menu !== except) menu.open = false; });
        menus.forEach((menu) => {
            menu.addEventListener('toggle', () => { if (menu.open) closeAll(menu); });
            menu.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => { menu.open = false; }));
        });
        document.addEventListener('click', (event) => { menus.forEach((menu) => { if (menu.open && !menu.contains(event.target)) menu.open = false; }); });
        document.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape') return;
            const open = menus.find((menu) => menu.open);
            if (open) { open.open = false; open.querySelector('summary').focus(); }
        });

        // The header marks the section being read: the last one begun in the top 40% of the screen.
        const spyLinks = [...document.querySelectorAll('.k-nav a[href^="#"], .k-menu__links a[href^="#"]')]
            .filter((link) => link.hash.length > 1 && document.getElementById(link.hash.slice(1)));
        if (spyLinks.length) {
            const ids = [...new Set(spyLinks.map((link) => link.hash.slice(1)))];
            const sections = ids.map((id) => document.getElementById(id));
            const mark = () => {
                let current = null;
                sections.forEach((section) => { if (section.getBoundingClientRect().top < innerHeight * 0.4) current = section.id; });
                if (innerHeight + scrollY >= root.scrollHeight - 2) current = ids[ids.length - 1];
                spyLinks.forEach((link) => link.classList.toggle('is-active', link.hash.slice(1) === current));
            };
            addEventListener('scroll', mark, { passive: true });
            mark();
        }

        // Blocks fade in as they reach the screen (CSS skips this with reduced motion).
        const reveal = [...document.querySelectorAll('.k-reveal, .k-reveal-group')];
        if ('IntersectionObserver' in window) {
            const observer = new IntersectionObserver((entries) => entries.forEach((entry) => {
                if (entry.isIntersecting) { entry.target.classList.add('is-visible'); observer.unobserve(entry.target); }
            }), { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });
            reveal.forEach((el) => observer.observe(el));
        } else {
            reveal.forEach((el) => el.classList.add('is-visible'));
        }

        // Copy buttons: data-copy="#id" copies that element's text.
        document.querySelectorAll('[data-copy]').forEach((button) => button.addEventListener('click', async () => {
            const target = document.querySelector(button.dataset.copy);
            if (!target) return;
            const text = target.textContent.trim();
            try {
                await navigator.clipboard.writeText(text);
            } catch (e) {
                const area = Object.assign(document.createElement('textarea'), { value: text });
                area.setAttribute('readonly', '');
                area.className = 'k-sr-only';
                document.body.append(area);
                area.select();
                try { document.execCommand('copy'); } catch (err) { /* nothing more to try */ }
                area.remove();
            }
            button.classList.add('is-copied');
            const label = button.getAttribute('aria-label');
            button.setAttribute('aria-label', 'Copied');
            setTimeout(() => { button.classList.remove('is-copied'); if (label) button.setAttribute('aria-label', label); }, 1600);
        }));

        // Screenshot viewer: <a href="big.jpg" data-lightbox><img alt="..."></a>. Without scripts
        // the link opens the image itself.
        let viewer = null;
        const openViewer = (link) => {
            if (!viewer) {
                viewer = document.createElement('dialog');
                viewer.className = 'k-lightbox';
                viewer.setAttribute('aria-label', 'Image');
                const close = document.createElement('button');
                close.type = 'button';
                close.className = 'k-icon-btn k-lightbox__close';
                close.setAttribute('aria-label', 'Close');
                close.append(icon('x'));
                close.addEventListener('click', () => viewer.close());
                const figure = document.createElement('figure');
                figure.append(document.createElement('img'), document.createElement('figcaption'));
                viewer.append(close, figure);
                viewer.addEventListener('click', (event) => { if (event.target === viewer) viewer.close(); });
                document.body.append(viewer);
            }
            const thumb = link.querySelector('img');
            const caption = link.closest('figure') && link.closest('figure').querySelector('figcaption');
            const img = viewer.querySelector('img');
            img.src = link.href;
            // A drawn picture (an inline <svg>) has no <img> to borrow from: the link's own label says it (1.4.0).
            img.alt = thumb ? thumb.alt : (link.getAttribute('aria-label') || '');
            viewer.querySelector('figcaption').textContent = caption ? caption.textContent.trim() : '';
            viewer.showModal();
        };
        document.querySelectorAll('a[data-lightbox]').forEach((link) => link.addEventListener('click', (event) => {
            if (typeof HTMLDialogElement !== 'function') return;
            event.preventDefault();
            openViewer(link);
        }));

        // Dialogs (1.4.0): <a href="/page/#part" data-dialog="#id"> opens <dialog class="k-dialog" id="id"> as a modal,
        // so a long text can stay one button on the page. [data-dialog-close], Escape and a click on the dimmed page
        // close it, and the focus goes back to the link. Without scripts, or without <dialog>, the link is followed:
        // the same text has to live at its href too (a site partial used in both places keeps them one text).
        document.querySelectorAll('[data-dialog]').forEach((opener) => opener.addEventListener('click', (event) => {
            const dialog = document.querySelector(opener.dataset.dialog);
            if (!dialog || typeof dialog.showModal !== 'function') return;
            event.preventDefault();
            dialog.showModal();
            dialog.addEventListener('close', () => opener.focus(), { once: true });
        }));
        document.querySelectorAll('dialog.k-dialog').forEach((dialog) => {
            dialog.querySelectorAll('[data-dialog-close]').forEach((button) => button.addEventListener('click', () => dialog.close()));
            // The dialog itself has no padding (its __inner has), so a click whose target is the dialog fell on the backdrop.
            dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.close(); });
        });

        // Video posters: <a class="k-video__poster" href="https://www.youtube.com/watch?v=ID" data-video="ID">.
        // Nothing is loaded from YouTube until the poster is pressed; then a youtube-nocookie player
        // replaces it. Without scripts the link opens the video on YouTube.
        document.querySelectorAll('[data-video]').forEach((poster) => poster.addEventListener('click', (event) => {
            const id = poster.dataset.video || '';
            if (!/^[A-Za-z0-9_-]{11}$/.test(id)) return;
            event.preventDefault();
            const frame = document.createElement('iframe');
            frame.className = 'k-video__frame';
            frame.src = 'https://www.youtube-nocookie.com/embed/' + id + '?autoplay=1&rel=0&playsinline=1';
            frame.title = poster.dataset.videoTitle || poster.getAttribute('aria-label') || 'Video';
            frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
            frame.allowFullscreen = true;
            frame.referrerPolicy = 'strict-origin-when-cross-origin';
            poster.replaceWith(frame);
            frame.focus();
        }));

        // Back to top, once the first screen is scrolled past.
        const up = document.querySelector('[data-to-top]');
        if (up) {
            const place = () => up.classList.toggle('is-shown', scrollY > 900);
            addEventListener('scroll', place, { passive: true });
            up.addEventListener('click', () => scrollTo({ top: 0 }));
            place();
        }
    };

    // The app frame (1.2.0): [data-k-side-fold] folds the side menu to its icons (wide screens, remembered under
    // "k-side"); [data-k-side-open] opens it as a drawer (narrow screens), closed by its backdrop, Escape or a link
    // followed. A side link whose href is the page's address or hash is marked aria-current, and its group opens.
    const app = () => {
        const side = document.querySelector('[data-k-side]');
        if (!side) return;
        const folds = [...document.querySelectorAll('[data-k-side-fold]')];
        const showFold = () => folds.forEach((button) => button.setAttribute('aria-pressed', String(root.getAttribute('data-k-side') === 'folded')));
        folds.forEach((button) => button.addEventListener('click', () => {
            const folded = root.getAttribute('data-k-side') === 'folded';
            if (folded) root.removeAttribute('data-k-side'); else root.setAttribute('data-k-side', 'folded');
            try { if (folded) localStorage.removeItem('k-side'); else localStorage.setItem('k-side', 'folded'); } catch (e) { /* not kept */ }
            showFold();
        }));
        showFold();
        let backdrop = document.querySelector('.k-side-backdrop');
        if (!backdrop) {
            backdrop = document.createElement('div');
            backdrop.className = 'k-side-backdrop';
            backdrop.hidden = true;
            side.after(backdrop);
        }
        const opens = [...document.querySelectorAll('[data-k-side-open]')];
        const setOpen = (open) => {
            side.classList.toggle('is-open', open);
            backdrop.hidden = !open;
            opens.forEach((button) => button.setAttribute('aria-expanded', String(open)));
        };
        opens.forEach((button) => button.addEventListener('click', () => setOpen(!side.classList.contains('is-open'))));
        backdrop.addEventListener('click', () => setOpen(false));
        side.querySelectorAll('a[href]').forEach((link) => link.addEventListener('click', () => setOpen(false)));
        document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && side.classList.contains('is-open')) setOpen(false); });
        const mark = () => {
            const here = location.hash || location.pathname;
            side.querySelectorAll('a[href]').forEach((link) => {
                const href = link.getAttribute('href');
                const current = href === here || (href.length > 1 && here.startsWith(href + '/'));
                if (current) {
                    link.setAttribute('aria-current', 'page');
                    const group = link.closest('details');
                    if (group) group.open = true;
                } else link.removeAttribute('aria-current');
            });
        };
        addEventListener('hashchange', mark);
        mark();
    };

    if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', init); document.addEventListener('DOMContentLoaded', app); }
    else { init(); app(); }
})();
