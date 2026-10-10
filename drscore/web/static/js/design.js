/**
 * Report designer UI interactions (spec section 7).
 * Pure JavaScript, CSP-compliant without inline scripts.
 */
(function () {
  'use strict';

  function renumberRows(tbody, prefix) {
    if (!tbody) return;
    // A row cloned from the <template> carries __INDEX__ until it gets its number here.
    var pattern = new RegExp('^' + prefix + '-(\\d+|__INDEX__)-');
    var rows = tbody.querySelectorAll('tr');
    rows.forEach(function (row, index) {
      var elements = row.querySelectorAll('input, select, textarea, label');
      elements.forEach(function (el) {
        if (el.name) {
          el.name = el.name.replace(pattern, prefix + '-' + index + '-');
        }
        if (el.id) {
          el.id = el.id.replace(pattern, prefix + '-' + index + '-');
        }
        if (el.htmlFor) {
          el.htmlFor = el.htmlFor.replace(pattern, prefix + '-' + index + '-');
        }
      });
    });
  }

  function addRow(templateId, tbodyId, prefix) {
    var template = document.getElementById(templateId);
    var tbody = document.getElementById(tbodyId);
    if (!template || !tbody) return;

    var clone = template.content.cloneNode(true);
    var tr = clone.querySelector('tr');
    if (!tr) return;

    tbody.appendChild(tr);
    renumberRows(tbody, prefix);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var addParamBtn = document.getElementById('btn-add-param');
    if (addParamBtn) {
      addParamBtn.addEventListener('click', function (e) {
        e.preventDefault();
        addRow('param-row-template', 'params-table-body', 'params');
      });
    }

    var addColBtn = document.getElementById('btn-add-column');
    if (addColBtn) {
      addColBtn.addEventListener('click', function (e) {
        e.preventDefault();
        addRow('column-row-template', 'columns-table-body', 'columns');
      });
    }

    document.addEventListener('click', function (e) {
      var removeParamBtn = e.target.closest('.btn-remove-param');
      if (removeParamBtn) {
        e.preventDefault();
        var trParam = removeParamBtn.closest('tr');
        if (trParam) {
          var tbodyParam = trParam.parentNode;
          trParam.remove();
          renumberRows(tbodyParam, 'params');
        }
        return;
      }

      var removeColBtn = e.target.closest('.btn-remove-column');
      if (removeColBtn) {
        e.preventDefault();
        var trCol = removeColBtn.closest('tr');
        if (trCol) {
          var tbodyCol = trCol.parentNode;
          trCol.remove();
          renumberRows(tbodyCol, 'columns');
        }
        return;
      }
    });
  });
})();
