// KaTeX's MathML output keeps the existing style security policy intact.
window.renderArticleMath = () => {
  const body = document.getElementById("article-body");
  if (!body || !window.renderMathInElement) return;
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
