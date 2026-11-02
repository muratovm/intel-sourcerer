// KaTeX's MathML output keeps the existing style security policy intact.
// Equations shrink to fit the column, but stay readable; beyond that they swipe.
const SMALLEST_MATH_SCALE = 0.6;
let fittedWidth = 0;
function fitDisplayMath(body) {
  fittedWidth = body.clientWidth;
  for (const element of body.querySelectorAll?.('.article-math[data-display="true"]') || []) {
    element.style.fontSize = "";
    element.classList.remove("math-swipe");
    if (!element.clientWidth) continue;
    let scale = 1;
    // Size doesn't map exactly to width, so a second pass catches any remainder.
    for (let pass = 0; pass < 2 && element.scrollWidth > element.clientWidth; pass += 1) {
      scale = Math.max(SMALLEST_MATH_SCALE, (scale * element.clientWidth) / element.scrollWidth);
      element.style.fontSize = `${scale}em`;
    }
    if (element.scrollWidth > element.clientWidth) element.classList.add("math-swipe");
  }
}
function watchMathWidth(body) {
  if (!window.ResizeObserver || body.dataset?.mathWatched) return;
  body.dataset.mathWatched = "true";
  new window.ResizeObserver(() => {
    if (body.clientWidth !== fittedWidth) fitDisplayMath(body);
  }).observe(body);
}
window.renderArticleMath = () => {
  const body = document.getElementById("article-body");
  if (!body) return;
  // Clean-HTML articles mark their math explicitly; text is never scanned for it.
  for (const element of body.querySelectorAll?.(".article-math") || []) {
    window.katex?.render(element.dataset.tex, element, {
      displayMode: element.dataset.display === "true",
      output: "mathml",
      throwOnError: false,
      trust: false,
      strict: "ignore",
      maxExpand: 1000,
      maxSize: 20,
    });
  }
  fitDisplayMath(body);
  watchMathWidth(body);
  if (body.dataset?.bodyFormat === "html" || !window.renderMathInElement) return;
  window.renderMathInElement(body, {
    delimiters: [
      {left: "$$", right: "$$", display: true},
      {left: "\\[", right: "\\]", display: true},
      {left: "\\(", right: "\\)", display: false},
      {left: "$", right: "$", display: false},
    ],
    output: "mathml",
    throwOnError: false,
    trust: false,
    strict: "ignore",
    maxExpand: 1000,
    maxSize: 20,
    ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "a"],
  });
};
