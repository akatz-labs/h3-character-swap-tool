// Optional host capability: local execution never requests or displays credits.
const generationPrices = new WeakMap();
function generationPriceButton(button, input, disabled = button.disabled) {
  if (!adapter.generationPrice) return;
  const key = JSON.stringify([adapter.identity(), S.batch, input]);
  let state = generationPrices.get(button);
  if (!state || state.key !== key || state.until < Date.now()) {
    // Refresh unchanged inputs in the background. Submission still verifies the server quote.
    state = {key, input, disabled, price: state?.key === key ? state.price : null, prices:state?.key === key ? state.prices : undefined, error: '', until: Infinity};
    generationPrices.set(button, state);
    const current = state;
    Promise.all((Array.isArray(input)?input:[input]).map(value=>adapter.generationPrice(value))).then(prices => {
      const price=prices.reduce((sum,value)=>sum+value,0);
      if (generationPrices.get(button) !== current || !button.isConnected) return;
      current.price = price;
      current.prices = prices;
      current.until = Date.now() + 60000;
      paintGenerationPrice(button, current);
    }, error => {
      if (generationPrices.get(button) !== current || !button.isConnected) return;
      current.price = null;
      current.error = error.message;
      current.until = Date.now() + 5000;
      paintGenerationPrice(button, current);
    });
  }
  state.disabled = disabled;
  paintGenerationPrice(button, state);
}
function paintGenerationPrice(button, state) {
  let badge = button.querySelector('.generation-credit-cost');
  if (!badge) { badge = document.createElement('small'); badge.className = 'generation-credit-cost'; button.append(badge); }
  const text = state.price === null ? (state.error ? 'Price unavailable' : '… credits') : `${state.price} ${state.price === 1 ? 'credit' : 'credits'}`;
  if (badge.textContent !== text) badge.textContent = text;
  badge.title = state.error || 'Credits used by this generation';
  button.disabled = state.disabled || state.price === null;
}
function generationPriceInput(button) {
  if (!adapter.generationPrice) return {};
  const state = generationPrices.get(button);
  if (!state || state.price === null) throw Error('Wait for the generation price to load.');
  return {expected_price: state.price,...(Array.isArray(state.input)?{expected_prices:state.prices}: {})};
}
window.addEventListener('h3-generation-pricing-changed', () => {
  for (const button of $$('.generation-credit-cost').map(b => b.closest('button'))) {
    const state = generationPrices.get(button);
    if (!state) continue;
    generationPrices.delete(button);
    generationPriceButton(button, state.input, state.disabled);
  }
}, {signal: lifecycle.signal});
