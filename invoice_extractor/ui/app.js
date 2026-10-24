/* Shared vanilla helpers for all UI pages (no framework, no build step).
 * Loaded via <script src="/ui/app.js"> before each page's IIFE; exposes window.App.
 * node --check this file together with the pages after any change. */
(function () {
  "use strict";
  function $(id) { return document.getElementById(id); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; });
  }

  // Flag-style toast: icon + message + optional action + dismiss. Error kinds
  // ("err" and "error" both accepted) persist until dismissed; info auto-hides.
  function toast(msg, kind, action) {
    var t = $("toast"); if (!t) return;
    t.textContent = "";
    var bad = kind === "err" || kind === "error";
    t.className = "toast show" + (bad ? " err" : "");
    t.setAttribute("role", bad ? "alert" : "status");
    var ic = document.createElement("span");
    ic.className = "t-ic"; ic.setAttribute("aria-hidden", "true");
    ic.innerHTML = '<svg aria-hidden="true"><use href="/ui/icons.svg#' +
      (bad ? "i-alert" : "i-check") + '"/></svg>';
    t.append(ic);
    t.append(document.createTextNode(msg));
    if (action) {
      var ab = document.createElement("button");
      ab.type = "button"; ab.className = "toast-act"; ab.textContent = action.label;
      ab.addEventListener("click", function (e) { e.stopPropagation(); t.classList.remove("show"); action.fn(); });
      t.append(ab);
    }
    var x = document.createElement("button");
    x.type = "button"; x.className = "toast-x"; x.setAttribute("aria-label", "Dismiss message");
    x.innerHTML = '<svg aria-hidden="true"><use href="/ui/icons.svg#i-close"/></svg>';
    x.addEventListener("click", function (e) { e.stopPropagation(); t.classList.remove("show"); }); t.append(x);
    clearTimeout(t._h);
    if (!bad) t._h = setTimeout(function () { t.classList.remove("show"); }, 4000);
  }

  // Atlassian-style modal confirm: titled, danger-styled confirm, safe Cancel
  // focused by default, Tab trapped, Esc/veil-click cancel with focus restore.
  function confirmDialog(o) {
    return new Promise(function (resolve) {
      var done = function (v) { document.removeEventListener("keydown", onKey, true); veil.remove(); opener.focus(); resolve(v); };
      var opener = document.activeElement;
      var veil = document.createElement("div"); veil.className = "modal-veil";
      veil.innerHTML = '<div class="modal" role="dialog" aria-modal="true" aria-labelledby="mdTitle"><h2 id="mdTitle"></h2><p></p>' +
        '<div class="modal-foot"><button type="button" class="btn secondary" data-x="no"></button>' +
        '<button type="button" class="btn danger" data-x="yes"></button></div></div>';
      veil.querySelector("h2").textContent = o.title; veil.querySelector("p").textContent = o.body;
      var no = veil.querySelector('[data-x="no"]'), yes = veil.querySelector('[data-x="yes"]');
      no.textContent = o.cancelLabel || "Cancel"; yes.textContent = o.confirmLabel || "Confirm";
      no.addEventListener("click", function () { done(false); });
      yes.addEventListener("click", function () { done(true); });
      veil.addEventListener("mousedown", function (e) { if (e.target === veil) done(false); });
      var onKey = function (e) {
        if (e.key === "Escape") { e.stopPropagation(); done(false); }
        else if (e.key === "Tab") { var f = [no, yes], i = f.indexOf(document.activeElement);
          e.preventDefault(); e.stopPropagation(); f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus(); }
      };
      document.addEventListener("keydown", onKey, true);
      document.body.append(veil); (o.danger === true ? yes : no).focus();
    });
  }

  // Database connectivity pill shared by settings + database pages.
  function pill(st) {
    var cls = !st.configured ? "p-queued" : (st.reachable ? "p-done" : "p-failed");
    var txt = !st.configured ? "Not connected" : (st.reachable ? "Connected" : "Can't reach it");
    return '<span class="pill ' + cls + '">' + txt + "</span>";
  }

  window.App = { $, esc: esc, toast: toast, confirmDialog: confirmDialog, pill: pill };
})();
