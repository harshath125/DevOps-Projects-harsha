/*
  AcademyLab — a SIMULATED teaching terminal.

  Nothing here touches a real server, cluster or cloud account. Every command is
  answered from a static script written for teaching, and every lab page says so.
  Data files live in docs/assets/js/labs/<id>.js and look like:

    AcademyLab.register("k8s-1", {
      title, host, goal, finish,
      steps: [{
        label, context,                    // what you are looking at
        cmd, alt: [],                       // accepted command(s); prefix with / for regex
        prompt, replies: {"sub":"out"},     // follow-up inputs inside the same step
        out, explain: {what, why, happened, risk, learn},
        hint, done,                         // extra coaching + completion line
        fail: {out, why, how, fix, verify, done, atFirst}   // injected failure (Troubleshooting mode or atFirst)
      }]
    });

  Output line prefixes are rendered as tones:  ++ ok   !! error   ## dim   -- warn
*/
window.AcademyLab = (function () {
  "use strict";
  var registry = {}, byId = {};

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (m) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[m]; }); }
  function out(line, cls) { return '<div class="tl ' + (cls || "out") + '">' + esc(line) + "</div>"; }
  function renderOut(text, cls) {
    if (text == null) return "";
    return String(text).replace(/\n+$/, "").split("\n").map(function (l) {
      var c = cls || "out";
      if (l.indexOf("++") === 0) c = "ok"; else if (l.indexOf("!!") === 0) c = "err";
      else if (l.indexOf("##") === 0 || l.indexOf("--") === 0) c = "dim";
      return out(l, c);
    }).join("");
  }
  function row(k, v) { return v ? '<p class="row"><span class="k">' + esc(k) + "</span> <span class='v'>" + v + "</span></p>" : ""; }
  function fmt(s) { return renderOut(s).replace(/class="tl out">([^<]*)<\/div>/g, function (_, t) { return t.replace(/\*\*([^*]+)\*\*/, "<b>$1</b>"); }); }

  function mount(host, id) {
    if (!host || host.__lab) return;
    var data = registry[id];
    if (!data) return;
    host.__lab = true;
    var st = { i: 0, mode: "normal", stuckOn: null, hist: [], histAt: -1 };
    var LS = "aca.lab." + id;
    function lsGet() { try { return JSON.parse(localStorage.getItem(LS) || "{}"); } catch (e) { return {}; } }
    function lsSet(o) { try { localStorage.setItem(LS, JSON.stringify(o)); } catch (e) { } }
    var saved = lsGet();
    if (typeof saved.i === "number" && saved.i > 0 && saved.i < (data.steps || []).length) st.i = saved.i;
    var bar = host.parentElement && host.parentElement.querySelector ? host.parentElement.querySelector("[data-lab-progress]") : null;
    if (host.closest) { var wrap = host.closest(".labhost"); if (wrap && !bar) bar = wrap.querySelector("[data-lab-progress]"); }

    host.innerHTML =
      '<div class="labscreen" tabindex="0"></div>' +
      '<div class="labcmd"><span class="tp">' + esc(data.prompt || (data.host || "devops@lab:~$")) + '</span><input class="in" spellcheck="false" autocomplete="off" autocapitalize="off" aria-label="Terminal input" placeholder="type the next command — or press Show command"></div>' +
      '<div class="labbtns">' +
      '<button class="labbtn primary" data-a="run">Run ↵</button>' +
      '<button class="labbtn" data-a="show">Show command</button>' +
      '<button class="labbtn" data-a="hint">Hint</button>' +
      '<button class="labbtn" data-a="next">Next →</button>' +
      '<button class="labbtn" data-a="back">← Back</button>' +
      '<button class="labbtn" data-a="mode" title="Troubleshooting mode injects realistic failures">Troubleshooting mode</button>' +
      '<button class="labbtn" data-a="reset">Reset</button>' +
      "</div>";
    var screen = host.querySelector(".labscreen"), inp = host.querySelector(".in");

    function progress() {
      var total = (data.steps || []).length || 1;
      if (bar) { bar.textContent = "step " + Math.min(st.i + 1, total) + " / " + total; }
      if (st.i >= total) { try { document.dispatchEvent(new CustomEvent("aca:lab-complete", { detail: { id: id } })); } catch (e) { } }
    }
    function scrollDown() { screen.scrollTop = screen.scrollHeight; }
    function say(html) { screen.insertAdjacentHTML("beforeend", html); scrollDown(); }

    function explainBlock(s) {
      var e = s.explain || {};
      var bits = row("what", esc(e.what || "")) + row("why", esc(e.why || "")) + row("what just happened", fmt(e.happened)) +
        row("risk", fmt(e.risk || e["can-go-wrong"] || "")) + row("remember", esc(e.learn || ""));
      return bits ? '<div class="labexp">' + bits + "</div>" : "";
    }
    function present(s) {
      if (!s) return;
      var html = '<div class="tl ctx"><b>CONTEXT.</b> ' + esc(s.context || "") + "</div>";
      if (s.label) html += "<h4>" + esc(s.label) + "</h4>";
      if (s.goal && s === (data.steps || [])[0]) html += '<div class="goal">GOAL: ' + esc(s.goal) + "</div>";
      if (s.cmd) html += out((data.prompt || data.host || "$") + " " + (s.cmdDisplay || s.cmd), "cmd");
      if (s.pre) html += renderOut(s.pre);
      html += explainBlock(s);
      if (s.done) html += '<div class="labbanner ok">✓ ' + esc(s.done) + "</div>";
      say(html);
    }
    function accept(s, val) {
      val = (val || "").trim().replace(/\s+/g, " ");
      if (!val) return false;
      var cands = [s.cmd].concat(s.alt || []).filter(Boolean);
      return cands.some(function (c) {
        if (c.charAt(0) === "/") { try { return new RegExp(c.slice(1), "i").test(val); } catch (e) { return false; } }
        var cc = c.replace(/\s+/g, " ").trim();
        return val === cc || val.indexOf(cc) === 0 || cc.indexOf(val) === 0 && val.length > Math.min(10, cc.length * 0.55);
      });
    }
    function succeed(s) {
      if (s.out) say(renderOut(s.out));
      if (s.explain && (s.explain.happened || s.explain.what)) say(explainBlock(s));
      if (s.done) say('<div class="labbanner ok">✓ ' + esc(s.done) + "</div>");
      st.i++; lsSet({ i: st.i }); progress();
      var nx = (data.steps || [])[st.i];
      if (nx) { say('<div class="tl dim">&nbsp;</div>'); present(nx); }
      else finish();
    }
    function failStep(s) {
      var f = s.fail || {};
      st.stuckOn = st.i;
      say(renderOut(f.out || "!! command failed"));
      var bits = row("why", fmt(f.why)) + row("how to check", fmt(f.how)) + row("fix", f.fix ? "<code>" + esc(f.fix) + "</code>" : "") + row("verify", f.verify ? "<code>" + esc(f.verify) + "</code>" : "") + (f.done ? '<div class="labbanner">now: ' + esc(f.done) + "</div>" : "");
      if (bits) say('<div class="labexp">' + bits + "</div>");
      say('<div class="labbanner">You are in the injected-failure path. Fix it, press <b>Run ↵</b> with the right command, or <b>Next →</b> to move on.</div>');
    }
    function finish() {
      say('<div class="labbanner ok">🎉 Lab complete. ' + esc(data.finish || "Well done.") + "</div>");
      lsSet({ i: (data.steps || []).length, complete: true });
      try {
        var d = JSON.parse(localStorage.getItem("aca.labs") || "{}"); d[id] = Date.now();
        localStorage.setItem("aca.labs", JSON.stringify(d));
      } catch (e) { }
      if (bar) bar.textContent = "complete ✓";
    }
    function reset() {
      st.i = 0; st.mode = "normal"; st.stuckOn = null;
      (data.steps || []).forEach(function (x) { if (x.fail) delete x.fail._used; });
      lsSet({}); screen.innerHTML = "";
      intro();
    }
    function intro() {
      screen.innerHTML = "";
      say('<div class="tl ctx"><b>' + esc(data.title || id) + "</b> — simulated teaching terminal. Nothing here changes real infrastructure.</div>");
      if (data.goal) say('<div class="goal">GOAL: ' + esc(data.goal) + "</div>");
      var s = (data.steps || [])[st.i];
      if (!s) { finish(); return; }
      present(s); progress();
    }
    function run() {
      var s = (data.steps || [])[st.i]; if (!s) return;
      var val = inp.value; inp.value = "";
      if (!val.trim()) { showCmd(); return; }
      st.hist.push(val); st.histAt = st.hist.length;
      say(out((data.prompt || data.host || "$") + " " + val, "cmd"));
      if (val === "help") return say(renderOut("Available: " + (s.cmd ? "the step command (or press Show command)" : "") + " · hint · show · steps · next · back · reset · mode"));
      if (val === "hint") return say(renderOut(s.hint || "Re-read the CONTEXT block — the command is described in words there."));
      if (val === "show") return showCmd();
      if (val === "next") return succeed(s);
      if (val === "back") { st.i = Math.max(0, st.i - 1); lsSet({ i: st.i }); progress(); intro(); return; }
      if (val === "reset") return reset();
      if (val === "mode") return toggleMode();
      if (val === "steps") return say(renderOut((data.steps || []).map(function (x, i) { return (i === st.i ? "→ " : "  ") + (i + 1) + ". " + (x.label || "step"); }).join("\n")));
      if (s.replies && s.replies[val.split(" ")[0]] !== undefined) return say(renderOut(s.replies[val.split(" ")[0]]));
      if (s.replies && s.replies[val] !== undefined) return say(renderOut(s.replies[val]));
      if (accept(s, val)) {
        if (s.fail && !s.fail._used && (st.mode === "trouble" || s.fail.atFirst)) { s.fail._used = true; return failStep(s); }
        return succeed(s);
      }
      if (s.fail && st.mode === "trouble" && !s.fail._used) { s.fail._used = true; return failStep(s); }
      say(renderOut(s.reject || "!! command not recognised in this step. Type <b>hint</b> for a nudge, <b>show</b> to reveal the exact command, or <b>next</b> to continue."));
    }
    function showCmd() { var s = (data.steps || [])[st.i]; if (!s) return; if (s.cmd) { inp.value = s.cmd; say(out("# shown: " + s.cmd, "dim")); } }
    function toggleMode() {
      st.mode = st.mode === "trouble" ? "normal" : "trouble";
      var b = host.querySelector('[data-a="mode"]');
      if (b) { b.classList.toggle("on", st.mode === "trouble"); b.textContent = st.mode === "trouble" ? "Troubleshooting: ON" : "Troubleshooting mode"; }
      say(renderOut(st.mode === "trouble" ? "## Troubleshooting mode ON — the next steps will inject realistic failures so you can practise recovering." : "## Troubleshooting mode OFF — straightforward path."));
    }
    host.addEventListener("click", function (e) {
      var b = e.target.closest("[data-a]"); if (!b) return;
      var a = b.getAttribute("data-a");
      if (a === "run") run(); else if (a === "show") showCmd();
      else if (a === "hint") { var s = (data.steps || [])[st.i]; say(renderOut("hint: " + ((s && s.hint) || "Look at the CONTEXT block above."))); }
      else if (a === "next") { var s2 = (data.steps || [])[st.i]; if (s2) succeed(s2); }
      else if (a === "back") { st.i = Math.max(0, st.i - 1); lsSet({ i: st.i }); progress(); intro(); }
      else if (a === "mode") toggleMode(); else if (a === "reset") reset();
    });
    inp.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); run(); }
      else if (e.key === "Tab") { e.preventDefault(); showCmd(); }
      else if (e.key === "ArrowUp") { if (st.histAt > 0) { st.histAt--; inp.value = st.hist[st.histAt] || ""; e.preventDefault(); } }
      else if (e.key === "ArrowDown") { if (st.histAt < st.hist.length - 1) { st.histAt++; inp.value = st.hist[st.histAt] || ""; } else { st.histAt = st.hist.length; inp.value = ""; } e.preventDefault(); }
    });
    intro();
  }

  function ensureData(id) {
    var s = doc.createElement("script");
    s.src = "assets/js/labs/" + id + ".js";
    s.onerror = function () { var h = byId[id]; if (h) h.innerHTML = '<div class="laberr" style="padding:.8rem">lab data file <b>assets/js/labs/' + id + ".js</b> is missing.</div>"; };
    doc.head.appendChild(s);
  }
  var doc = document;
  function boot() {
    Array.prototype.forEach.call(doc.querySelectorAll("[data-lab-id]"), function (host) {
      var id = host.getAttribute("data-lab-id");
      byId[id] = host;
      if (registry[id]) return mount(host, id);
      ensureData(id);
      var tries = 0;
      (function poll() {
        if (registry[id]) return mount(host, id);
        if (++tries > 120) return;
        setTimeout(poll, 40);
      })();
    });
  }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot); else boot();

  return {
    register: function (id, data) {
      registry[id] = data;
      var host = byId[id] || doc.querySelector('[data-lab-id="' + id + '"]');
      if (host) mount(host, id);
    },
    mounted: function (id) { return !!registry[id]; },
  };
})();
