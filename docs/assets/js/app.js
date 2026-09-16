/* DevOps Academy — theme, drawer, progress, copy, checklists, lazy loaders, search. */
(function () {
  "use strict";
  var doc = document, root = doc.documentElement, body = doc.body;
  var PID = body.getAttribute("data-page") || "index";
  function ls(k, v) {
    try { if (v === undefined) return JSON.parse(localStorage.getItem(k) || "null"); localStorage.setItem(k, JSON.stringify(v)); } catch (e) { }
    return null;
  }

  /* ---------- theme ---------- */
  function applyTheme(t) { root.dataset.theme = t === "auto" ? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : t; root.setAttribute("data-theme-set", t); }
  applyTheme(ls("aca.theme") || "auto");
  body.addEventListener("click", function (e) {
    var b = e.target.closest("[data-theme-toggle]"); if (!b) return;
    var cur = ls("aca.theme") || "auto";
    var next = cur === "auto" ? (root.dataset.theme === "dark" ? "light" : "dark") : (cur === "dark" ? "light" : "dark");
    ls("aca.theme", next); applyTheme(next);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener && matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { if ((ls("aca.theme") || "auto") === "auto") applyTheme("auto"); });

  /* ---------- drawer ---------- */
  body.addEventListener("click", function (e) {
    if (e.target.closest("[data-drawer]")) { body.dataset.drawer = body.dataset.drawer === "1" ? "" : "1"; return; }
    if (body.dataset.drawer === "1" && (e.target.closest("[data-drawer-target] a") || e.target.tagName === "A")) body.dataset.drawer = "";
  });

  /* ---------- reading progress ---------- */
  var bar = doc.querySelector("[data-progress-bar]");
  function tick() {
    if (!bar) return;
    var h = doc.documentElement.scrollHeight - innerHeight;
    bar.style.width = (h > 40 ? Math.min(100, (scrollY / h) * 100) : 0) + "%";
  }
  addEventListener("scroll", tick, { passive: true }); addEventListener("resize", tick); tick();

  /* ---------- done marks (per page + sidebar dots) ---------- */
  var done = ls("aca.done") || {};
  function paintDots() {
    Array.prototype.forEach.call(doc.querySelectorAll("[data-done-dot]"), function (d) {
      var id = d.getAttribute("data-done-dot");
      d.classList.toggle("done", !!(id && done[id]));
    });
    var lbl = doc.querySelector("[data-progress-label]");
    if (lbl) {
      var total = doc.querySelectorAll("[data-done-dot]").length || 1;
      var n = 0; for (var k in done) if (done[k]) n++;
      lbl.textContent = Math.round((n / total) * 100) + "% · " + n + "/" + total;
    }
  }
  var mark = doc.querySelector("[data-mark-done]");
  if (mark) {
    mark.checked = !!done[PID];
    mark.addEventListener("change", function () { done[PID] = mark.checked; ls("aca.done", done); paintDots(); });
  }
  paintDots();

  /* ---------- copy buttons ---------- */
  function copyText(txt, btn, okMsg) {
    var doneIt = function () { var o = btn.textContent; btn.textContent = okMsg || "copied"; btn.classList.add("done"); setTimeout(function () { btn.textContent = o; btn.classList.remove("done"); }, 1600); };
    if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(txt).then(doneIt, function () { fallback(); }); } else fallback();
    function fallback() {
      var ta = doc.createElement("textarea"); ta.value = txt; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.left = "-9999px";
      doc.body.appendChild(ta); ta.select(); try { doc.execCommand("copy"); doneIt(); } catch (e) { alert("Copy failed — select the text manually."); } doc.body.removeChild(ta);
    }
  }
  body.addEventListener("click", function (e) {
    var c = e.target.closest("[data-copy]");
    if (c) { var w = c.closest(".codewrap"); var code = w && w.querySelector("code"); if (code) copyText(code.textContent, c); return; }
    var p = e.target.closest("[data-prompt-copy]");
    if (p) {
      var key = p.getAttribute("data-prompt-copy");
      var box = p.closest(".promptbox"); var pre = box && box.querySelector(".prompttext");
      if (pre) copyText(pre.textContent.replace(/\s+$/, "") + "\n", p, "COPIED — paste into your coding agent");
    }
  });

  /* ---------- tickable checklists ---------- */
  var storeKey = "aca.check." + PID;
  var saved = ls(storeKey) || {};
  body.addEventListener("click", function (e) {
    var li = e.target.closest("ul.checklist li"); if (!li) return;
    if (e.target.tagName === "A") return;
    li.classList.toggle("tick");
    var idx = Array.prototype.indexOf.call(li.parentNode.children, li);
    saved[idx] = li.classList.contains("tick"); ls(storeKey, saved);
  });
  (function restore() {
    var ul = body.querySelector("ul.checklist"); if (!ul) return;
    for (var k in saved) { if (saved[k]) { var li = ul.children[k | 0]; if (li) li.classList.add("tick"); } }
  })();

  /* ---------- lazy loaders for lab / quiz / search ---------- */
  function once(name, src) {
    if (doc.querySelector("script[data-mod='" + name + "']")) return;
    var s = doc.createElement("script"); s.src = src; s.async = false; s.setAttribute("data-mod", name); doc.head.appendChild(s);
  }
  if (doc.querySelector("[data-lab-id]")) once("lab", "assets/js/lab.js");
  if (doc.querySelector("[data-quiz]")) once("quiz", "assets/js/quiz.js");
  if (doc.querySelector("[data-search-open]")) once("search-index", "assets/js/search-index.js");

  /* ---------- search ---------- */
  var modal = doc.querySelector("[data-search-modal]"), input = doc.querySelector("[data-search-input]"), res = doc.querySelector("[data-search-results]"), sel = -1;
  function openSearch() { if (!modal) return; modal.hidden = false; once("search-index", "assets/js/search-index.js"); setTimeout(function () { input.focus(); }, 30); }
  function closeSearch() { if (modal) modal.hidden = true; }
  body.addEventListener("click", function (e) { if (e.target.closest("[data-search-open]")) { e.preventDefault(); openSearch(); } });
  doc.addEventListener("keydown", function (e) {
    if (e.key === "/" && !/input|textarea/i.test((doc.activeElement || {}).tagName || "")) { e.preventDefault(); openSearch(); }
    if (e.key === "Escape") closeSearch();
    if (modal && !modal.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp") && res) {
      e.preventDefault(); var items = res.querySelectorAll("li"); if (!items.length) return;
      sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      Array.prototype.forEach.call(items, function (li, i) { li.classList.toggle("sel", i === sel); });
      var a = items[sel].querySelector("a"); if (a && a.scrollIntoView) a.scrollIntoView({ block: "nearest" });
    }
    if (modal && !modal.hidden && e.key === "Enter" && sel >= 0 && res) { var cur = res.querySelector("li.sel a"); if (cur) location.href = cur.href; }
  });
  if (modal) modal.addEventListener("click", function (e) { if (e.target === modal) closeSearch(); });
  if (input) input.addEventListener("input", function () {
    var q = input.value.trim().toLowerCase(); sel = -1;
    if (!res) return;
    var idx = window.ACADEMY_INDEX || [];
    if (q.length < 2) { res.innerHTML = "<li class='s-none'><span class='ss'>Type at least two letters — it searches titles, section headings and body text.</span></li>"; return; }
    function score(p) {
      var t = (p.title || "").toLowerCase(), s = (p.sections || []).join(" ").toLowerCase(), b = (p.text || "").toLowerCase(), n = 0;
      if (t.indexOf(q) >= 0) n += 60;
      if (s.indexOf(q) >= 0) n += 25;
      var i = b.indexOf(q), hits = 0, from = 0;
      while (i >= 0 && hits < 8) { n += 6; hits++; if (!from && i > 60) { } from = i; i = b.indexOf(q, i + 1); }
      if (from > 0) n += 2;
      return n;
    }
    var hits = idx.map(function (p) { return { p: p, n: score(p) }; }).filter(function (x) { return x.n > 0; }).sort(function (a, b) { return b.n - a.n; }).slice(0, 12);
    if (!hits.length) { res.innerHTML = "<li class='s-none'><span class='ss'>Nothing found for “" + esc(q) + "”. Try: <em>502</em>, <em>state lock</em>, <em>readiness</em>, <em>trivy</em>, <em>OOMKilled</em>.</span></li>"; return; }
    res.innerHTML = hits.map(function (x) {
      var snip = snippet(x.p.text, q);
      return "<li><a href='" + x.p.id + ".html'><span class='st'>" + esc(x.p.title) + "</span><span class='ss'>" + esc(x.p.section || "") + (x.p.time ? " · " + esc(x.p.time) : "") + "<br>" + snip + "</span></a></li>";
    }).join("");
    function esc(s2) { return String(s2 == null ? "" : s2).replace(/[&<>"]/g, function (m) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[m]; }); }
    function snippet(text, needle) {
      var t = text || "", i = t.toLowerCase().indexOf(needle);
      if (i < 0) return esc(t.slice(0, 130)) + "…";
      var from = Math.max(0, i - 55), out = t.slice(from, Math.min(t.length, i + 90));
      return (from > 0 ? "…" : "") + esc(out).replace(new RegExp("(" + needle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "ig"), "<mark>$1</mark>") + "…";
    }
  });

  /* ---------- external link affordance + smooth anchors ---------- */
  Array.prototype.forEach.call(doc.querySelectorAll(".prose a[href^='#']"), function (a) {
    a.addEventListener("click", function (e) {
      var el = doc.getElementById(a.getAttribute("href").slice(1));
      if (el) { e.preventDefault(); el.scrollIntoView({ behavior: "smooth", block: "start" }); history.replaceState(null, "", a.getAttribute("href")); }
    });
  });
})();
