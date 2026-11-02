const fs = require('node:fs');
const assert = require('node:assert/strict');
global.window = {};
global.document = { getElementById: () => ({}) };
eval(fs.readFileSync('src/intel_sourcerer/ui/workspace/math.js', 'utf8'));
let options;
window.renderMathInElement = (_body, config) => { options = config; };
window.renderArticleMath();
assert.deepEqual(options.delimiters, [
  {left:'$$',right:'$$',display:true},
  {left:'\\[',right:'\\]',display:true},
  {left:'\\(',right:'\\)',display:false},
  {left:'$',right:'$',display:false},
]);
assert(!options.delimiters.some(d => d.left === '(' || d.left === '['));
const katex = require('../../src/intel_sourcerer/ui/vendor/katex/katex.min.js');
const inline = katex.renderToString('x_i', {output:'mathml',displayMode:false});
assert(!inline.includes('display="block"'));
const display = katex.renderToString('x_i', {output:'mathml',displayMode:true});
assert(display.includes('display="block"'));
// A wide display equation shrinks to fit, but never below the readable floor.
function equation(contentWidth) {
  const classes = new Set();
  const element = { clientWidth: 300, style: {}, classes, classList: { add: name => classes.add(name), remove: name => classes.delete(name) } };
  Object.defineProperty(element, 'scrollWidth', { get() {
    const size = parseFloat(element.style.fontSize) || 1;
    return Math.round(contentWidth * size);
  } });
  return element;
}
const fits = equation(450), floor = equation(1200), narrow = equation(200);
document.getElementById = () => ({
  clientWidth: 300, dataset: {},
  querySelectorAll: selector => selector.includes('data-display') ? [fits, floor, narrow] : [],
});
window.renderArticleMath();
assert(fits.scrollWidth <= fits.clientWidth);
assert.equal(floor.style.fontSize, '0.6em');
assert(floor.classes.has('math-swipe'));
assert(!fits.classes.has('math-swipe'));
assert.equal(narrow.style.fontSize, '');
console.log('Math delimiter and inline rendering checks passed');
