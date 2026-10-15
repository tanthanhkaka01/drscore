// BI page: the parameters of the reports a dashboard also reads, put on the report's form.
// A parameter is dragged from the left onto the form (or added with +), ordered by dragging or
// with the arrows, and taken off with x or by dragging it back. One with the same name, label
// and type as an input already on the form shares that input: it is marked so. The form is sent
// as hidden "pick" inputs, in their order.
(function () {
  "use strict";

  var form = document.getElementById("dashboard-params");
  if (!form) return;
  var picked = document.getElementById("picked");
  var hint = picked.querySelector(".drs-drop-hint");
  var skeleton = document.getElementById("picked-item");
  var chips = Array.prototype.slice.call(form.querySelectorAll(".drs-chip"));
  var ownKeys = {};
  form.querySelectorAll("[data-own-key]").forEach(function (o) { ownKeys[o.getAttribute("data-own-key")] = true; });

  function items() { return Array.prototype.slice.call(picked.querySelectorAll("[data-pick]")); }
  function itemOf(pick) { return items().filter(function (i) { return i.getAttribute("data-pick") === pick; })[0]; }
  function chipOf(pick) { return chips.filter(function (c) { return c.getAttribute("data-pick") === pick; })[0]; }

  // What each item shares, which chips are on the form, and the hint of an empty form.
  function refresh() {
    var seen = {};
    items().forEach(function (item) {
      var key = item.getAttribute("data-key");
      var badge = item.querySelector("[data-shared]");
      var text = ownKeys[key] ? form.getAttribute("data-text-shared-own")
        : seen[key] ? form.getAttribute("data-text-shared-above") : "";
      badge.textContent = text;
      badge.classList.toggle("d-none", !text);
      seen[key] = true;
    });
    chips.forEach(function (chip) {
      var on = !!itemOf(chip.getAttribute("data-pick"));
      chip.classList.toggle("drs-picked", on);
      chip.querySelector("[data-add]").disabled = on;
    });
    hint.classList.toggle("d-none", items().length > 0);
  }

  function add(chip, before) {
    var pick = chip.getAttribute("data-pick");
    if (itemOf(pick)) return;
    var item = skeleton.content.firstElementChild.cloneNode(true);
    item.setAttribute("data-pick", pick);
    item.setAttribute("data-key", chip.getAttribute("data-key"));
    item.querySelector("input[name=pick]").value = pick;
    item.querySelector("[data-text]").innerHTML = chip.querySelector("[data-text]").innerHTML;
    item.querySelector("[data-member]").textContent = chip.getAttribute("data-member");
    item.querySelector("[data-member-name]").textContent = chip.getAttribute("data-member-name");
    picked.insertBefore(item, before || hint);
    refresh();
  }

  // The item a dragged one goes before, from the pointer's height.
  function itemAfter(y) {
    return items().filter(function (i) { return !i.classList.contains("drs-dragging"); }).filter(function (i) {
      var box = i.getBoundingClientRect();
      return y < box.top + box.height / 2;
    })[0] || null;
  }

  form.addEventListener("click", function (e) {
    var button = e.target.closest("button[data-add], button[data-move], button[data-remove]");
    if (!button) return;
    var item = button.closest("[data-pick]");
    if (button.hasAttribute("data-add")) add(item);
    else if (button.hasAttribute("data-remove")) { item.remove(); refresh(); }
    else {
      var list = items();
      var at = list.indexOf(item) + Number(button.getAttribute("data-move"));
      if (at < 0 || at >= list.length) return;
      picked.insertBefore(item, at > list.indexOf(item) ? list[at].nextSibling : list[at]);
      refresh();
    }
  });

  // ---------------------------------------------------------------- drag and drop

  var dragged = null;   // {pick, from: "chip" | "item"}
  form.addEventListener("dragstart", function (e) {
    var node = e.target.closest && e.target.closest("[data-pick]");
    if (!node) return;
    dragged = { pick: node.getAttribute("data-pick"), from: node.classList.contains("drs-chip") ? "chip" : "item" };
    if (dragged.from === "item") node.classList.add("drs-dragging");
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", dragged.pick);
  });
  form.addEventListener("dragend", function () {
    form.querySelectorAll(".drs-dragging").forEach(function (n) { n.classList.remove("drs-dragging"); });
    picked.classList.remove("drs-over");
    dragged = null;
  });
  picked.addEventListener("dragover", function (e) {
    if (!dragged) return;
    e.preventDefault();
    picked.classList.add("drs-over");
  });
  picked.addEventListener("dragleave", function (e) {
    if (!picked.contains(e.relatedTarget)) picked.classList.remove("drs-over");
  });
  picked.addEventListener("drop", function (e) {
    if (!dragged) return;
    e.preventDefault();
    picked.classList.remove("drs-over");
    var before = itemAfter(e.clientY) || hint;
    if (dragged.from === "chip") add(chipOf(dragged.pick), before);
    else { picked.insertBefore(itemOf(dragged.pick), before); refresh(); }
  });
  // An item dragged back onto the parameters of the reports leaves the form.
  var source = chips.length ? chips[0].closest(".col-lg-6") : null;
  if (source) {
    source.addEventListener("dragover", function (e) { if (dragged && dragged.from === "item") e.preventDefault(); });
    source.addEventListener("drop", function (e) {
      if (!dragged || dragged.from !== "item") return;
      e.preventDefault();
      itemOf(dragged.pick).remove();
      refresh();
    });
  }

  refresh();
})();
