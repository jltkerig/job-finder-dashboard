// Text boxes marked data-autogrow are always as tall as the text inside them, so nothing needs dragging or scrolling.
// A box inside a closed panel is sized when the panel opens (the resize observer fires then).
(function () {
  "use strict";
  function grow(box) {
    if (!box.offsetParent) return;  // hidden: nothing to measure yet
    box.style.height = "auto";
    box.style.height = box.scrollHeight + (box.offsetHeight - box.clientHeight) + "px";
  }
  document.querySelectorAll("textarea[data-autogrow]").forEach(function (box) {
    box.style.overflowY = "hidden"; box.style.resize = "none";
    box.addEventListener("input", function () { grow(box); });
    if (window.ResizeObserver) {
      var width = 0;
      new ResizeObserver(function () { if (box.offsetWidth !== width) { width = box.offsetWidth; grow(box); } }).observe(box);
    }
    grow(box);
  });
})();
