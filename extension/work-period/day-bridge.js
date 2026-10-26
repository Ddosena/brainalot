// Keep the established Day view on the same effective schedule as Plan.
// Raw work-store snapshots remain raw, so board saves cannot persist a projection.
export function createDayBridge(root, ctx, {createConstructor, api, loadingText = ''}) {
  let delegate = null, effective = null, disposed = false, readNumber = 0, acceptedRead = 0;
  let pending = null,initialError=null;
  const projected = Object.create(ctx);
  Object.defineProperty(projected, 'state', {get() {
    return {...ctx.state, planning: effective?.planning ?? null};
  }});
  async function refresh() {
    const number = ++readNumber;
    const result = await api('/api/work/period/day');
    if (disposed || number < acceptedRead) return;
    acceptedRead = number;
    if (!result || !Object.hasOwn(result, 'planning') || !Number.isInteger(result.revision)) {
      throw new Error('Invalid Work day projection');
    }
    if (effective && result.revision < effective.revision) return;
    effective = result;
    initialError=null;
    if (!delegate) delegate = createConstructor(root, projected);
  }
  projected.action = async (...args) => {
    const result = await ctx.action(...args);
    await refresh();
    return result;
  };
  projected.refreshWork = async (...args) => {
    const result = await ctx.refreshWork?.(...args);
    await refresh();
    return result;
  };
  function schedule(method) {
    pending = refresh().then(() => {
      if (!disposed) delegate?.[method]?.();
    }).catch(error => {
      if (disposed) return;
      initialError=error;
      if (delegate) ctx.toast?.(error.message);
      else root.textContent = error.message || loadingText;
    });
    return pending;
  }
  root.textContent = loadingText;
  void schedule('render');
  return {
    render() {return schedule('render');},
    remoteChanged() {return schedule('remoteChanged');},
    async ready() {await pending;if(!delegate)throw initialError||new Error(loadingText);},
    async prepareLeave() {await pending;return delegate?.prepareLeave?.();},
    destroy() {disposed = true; delegate?.destroy();},
  };
}
