/*
  AcademyQuiz — the "Test me" engine. Data file: assets/js/quizzes.js
  AcademyQuiz.register("id", { title, pass, passNote, questions: [{
      q, opts:[...], a: index, why, topic, type ("mcq"|"trouble"|"scenario"), level
  }]})
  Answers, score and last attempt stay in localStorage. No backend.
*/
window.AcademyQuiz = (function () {
  "use strict";
  var reg = {}, doc = document;
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (m) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[m]; }); }
  function key(id) { return "aca.quiz." + id; }
  function get(id) { try { return JSON.parse(localStorage.getItem(key(id)) || "{}"); } catch (e) { return {}; } }
  function set(id, o) { try { localStorage.setItem(key(id), JSON.stringify(o)); } catch (e) { } }

  function mount(host, id) {
    if (!host || host.__q) return;
    var data = reg[id]; if (!data) return;
    host.__q = true;
    var qs = data.questions || [], i = 0, right = 0, picked = null;
    var prev = get(id);
    host.innerHTML =
      '<div class="quizbar"><span>' + esc(data.title || "Quiz") + '</span><span class="qscore" data-score></span></div>' +
      '<div class="qbody" data-body></div>';
    var body = host.querySelector("[data-body]"), score = host.querySelector("[data-score]");

    function paint() {
      score.textContent = (i + 1) + " / " + qs.length + (right ? " · " + right + " correct" : "");
      var q = qs[i];
      if (!q) return done();
      var head = '<p class="qq"><span class="qtype">' + esc(q.type || q.topic || "question") + "</span> " + esc(q.q) + "</p>";
      var opts = '<div class="qopts">' + (q.opts || []).map(function (o, n) {
        return '<button class="qopt" data-i="' + n + '"><span class="key">' + "ABCDE"[n] + ".</span><span>" + esc(o) + "</span></button>";
      }).join("") + "</div>";
      body.innerHTML = head + opts + '<div class="qfoot"><button class="qbtn ghost" data-a="skip">Skip</button><span class="qmeta" data-meta></span></div>';
      picked = null;
    }
    function answer(n) {
      if (picked !== null) return;
      picked = n;
      var q = qs[i], btns = body.querySelectorAll(".qopt"), ok = n === q.a;
      Array.prototype.forEach.call(btns, function (b, k) {
        b.disabled = true;
        if (k === q.a) b.classList.add("right");
        else if (k === n) b.classList.add("wrong");
      });
      if (ok) right++;
      var why = '<div class="qwhy"><b>' + (ok ? "Correct." : "Not quite.") + "</b> " + esc(q.why || "") + "</div>";
      var nav = '<div class="qfoot"><button class="qbtn" data-a="next">' + (i + 1 >= qs.length ? "See result" : "Next question →") + '</button><span class="qmeta">' + (i + 1) + "/" + qs.length + "</span></div>";
      var meta = body.querySelector("[data-meta]"); if (meta) meta.parentNode.insertAdjacentHTML("beforebegin", why);
      if (meta) meta.parentNode.remove();
      body.insertAdjacentHTML("beforeend", nav);
      var st = get(id); st[i] = { p: n, ok: ok }; set(id, st);
    }
    function done() {
      var total = qs.length, pct = total ? Math.round((right / total) * 100) : 0;
      var pass = typeof data.pass === "number" ? data.pass : 70;
      var missed = Object.keys(get(id)).filter(function (k) { return get(id)[k].ok === false; });
      body.innerHTML =
        '<div class="qverdict"><p><b>' + right + " / " + total + "</b> (" + pct + "%) — " +
        (pct >= pass ? esc(data.passNote || "solid. You can move on.") : "below the " + pass + "% gate — review the marked sections and retake.") + "</p>" +
        (missed.length ? '<p style="font-size:.86rem;color:var(--ink-2)">Re-read: ' + missed.map(function (k) { return "<code>Q" + (parseInt(k, 10) + 1) + "</code>"; }).join(" ") + "</p>" : "") +
        '<div class="qfoot"><button class="qbtn" data-a="again">Try again</button><button class="qbtn ghost" data-a="review">Review all answers</button></div></div>';
      var st2 = get(id); st2._done = { pct: pct, at: Date.now() }; set(id, st2);
      score.textContent = pct + "%";
      body.__review = function () {
        body.innerHTML = '<div class="qresult">' + qs.map(function (q, n) {
          var a = get(id)[n];
          return '<div class="line ' + (a && a.ok ? "" : "no") + '"><b>Q' + (n + 1) + "</b><span>" + esc(q.q) + "</span></div>" +
            (a && a.ok ? "" : '<div class="line"><span style="color:var(--good)">answer: ' + "ABCDE"[q.a] + " — " + esc((q.opts || [])[q.a] || "") + "</span></div>" + '<div class="line">' + esc(q.why || "") + "</div>");
        }).join("") + '</div><div class="qfoot"><button class="qbtn" data-a="again">Try again</button></div>';
      };
    }
    host.addEventListener("click", function (e) {
      var o = e.target.closest(".qopt"); if (o) return answer(parseInt(o.getAttribute("data-i"), 10));
      var b = e.target.closest("[data-a]"); if (!b) return;
      var a = b.getAttribute("data-a");
      if (a === "next") { i++; paint(); }
      else if (a === "skip") { i++; paint(); }
      else if (a === "again") { i = 0; right = 0; set(id, {}); paint(); }
      else if (a === "review") { body.__review && body.__review(); }
    });
    if (prev._done && !location.hash) { /* keep last result visible in score chip */ score.textContent = "last: " + prev._done.pct + "%"; }
    paint();
  }
  function boot() { Array.prototype.forEach.call(doc.querySelectorAll("[data-quiz]"), function (h) { var id = h.getAttribute("data-quiz"); if (reg[id]) mount(h, id); }); }
  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot); else boot();
  return { register: function (id, data) { reg[id] = data; var h = doc.querySelector('[data-quiz="' + id + '"]'); if (h) mount(h, id); } };
})();
