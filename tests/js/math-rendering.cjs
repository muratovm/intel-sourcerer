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
console.log('Math delimiter and inline rendering checks passed');
