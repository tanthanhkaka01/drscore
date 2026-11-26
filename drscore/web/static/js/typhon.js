// The AI agent's chat (/ai): asks /api/ai/ask, shows the answer; the past conversations on the left.
// Every text comes from the page data (i18n); every answer is put in as text, never as HTML.
(function () {
  "use strict";
  var data = JSON.parse(document.getElementById("page-data").textContent);
  var msg = data.messages || {};
  var csrf = document.querySelector('meta[name="csrf-token"]').getAttribute("content");
  var chat = document.getElementById("ai-chat");
  var empty = document.getElementById("ai-empty");
  var form = document.getElementById("ai-form");
  var box = document.getElementById("ai-question");
  var send = document.getElementById("ai-send");
  var list = document.getElementById("ai-conversations");
  var conversationId = null;

  function t(key, fallback) { return msg[key] || fallback || key; }

  function bubble(role, text, extraClass) {
    if (empty) { empty.remove(); empty = null; }
    var row = document.createElement("div");
    row.className = "d-flex mb-3 " + (role === "user" ? "justify-content-end" : "justify-content-start");
    var inner = document.createElement("div");
    inner.className = "p-2 rounded " + (role === "user" ? "bg-primary-lt" : "bg-light") + (extraClass ? " " + extraClass : "");
    inner.style.maxWidth = "85%";
    inner.style.whiteSpace = "pre-wrap";
    if (role !== "user") {
      var who = document.createElement("div");
      who.className = "small text-secondary mb-1";
      who.textContent = data.name;
      inner.appendChild(who);
    }
    var body = document.createElement("div");
    body.textContent = text;
    inner.appendChild(body);
    row.appendChild(inner);
    chat.appendChild(row);
    chat.scrollTop = chat.scrollHeight;
    return row;
  }

  function clearChat() {
    chat.textContent = "";
    empty = null;
  }

  function loadList() {
    if (!list) return;
    fetch("/api/ai/conversations", { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : { conversations: [] }; })
      .then(function (answer) {
        list.textContent = "";
        answer.conversations.forEach(function (c) {
          var a = document.createElement("a");
          a.href = "#";
          a.className = "list-group-item list-group-item-action text-truncate" + (c.id === conversationId ? " active" : "");
          a.textContent = c.title;
          a.addEventListener("click", function (e) { e.preventDefault(); open(c.id); });
          list.appendChild(a);
        });
      });
  }

  function open(id) {
    fetch("/api/ai/conversations/" + id, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (answer) {
        if (!answer.turns) return;
        conversationId = answer.id;
        clearChat();
        answer.turns.forEach(function (turn) { bubble(turn.role, turn.text); });
        loadList();
      });
  }

  document.getElementById("ai-new").addEventListener("click", function () {
    conversationId = null;
    clearChat();
    loadList();
    box.focus();
  });

  box.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var question = box.value.trim();
    if (!question) return;
    bubble("user", question);
    box.value = "";
    send.disabled = true;
    var waiting = bubble("assistant", t("ai.thinking", "…"), "text-secondary");
    fetch("/api/ai/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ question: question, conversation_id: conversationId, page_path: data.from })
    })
      .then(function (r) { return r.json().then(function (body) { return { ok: r.ok, body: body }; }); })
      .then(function (res) {
        waiting.remove();
        if (!res.ok) {
          bubble("assistant", (res.body.error && res.body.error.message) || t("ai.failed"), "text-danger");
          return;
        }
        conversationId = res.body.conversation_id;
        bubble("assistant", res.body.refused ? t("ai.refused") : res.body.answer);
        loadList();
      })
      .catch(function () {
        waiting.remove();
        bubble("assistant", t("ai.failed"), "text-danger");
      })
      .then(function () { send.disabled = false; box.focus(); });
  });

  loadList();
})();
