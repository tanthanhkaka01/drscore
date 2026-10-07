// Home page: filters the report cards by name or code.
(function () {
  "use strict";
  var box = document.getElementById("report-search");
  if (!box) return;
  box.addEventListener("input", function () {
    var q = box.value.trim().toLowerCase();
    var anyVisible = false;
    document.querySelectorAll("[data-group]").forEach(function (group) {
      var visibleInGroup = 0;
      group.querySelectorAll("[data-report]").forEach(function (item) {
        var show = !q || item.getAttribute("data-search").indexOf(q) !== -1;
        item.classList.toggle("d-none", !show);
        if (show) visibleInGroup++;
      });
      group.classList.toggle("d-none", visibleInGroup === 0);
      if (visibleInGroup) anyVisible = true;
    });
    document.getElementById("no-match").classList.toggle("d-none", anyVisible);
  });
})();
