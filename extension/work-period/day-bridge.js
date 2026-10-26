// Keep the established Day view on the same effective schedule as Plan.
// Raw work-store snapshots remain raw, so board saves cannot persist a projection.
export function createDayBridge(root, ctx, {createConstructor, api, loadingText = '',setPoll=setInterval,clearPoll=clearInterval}) {
  let delegate = null, effective = null, disposed = false, readNumber = 0, acceptedRead = 0;
  let pending = null,initialError=null,inFlight=null,signature='',deliveredSignature='';
  const projected = Object.create(ctx);
  Object.defineProperty(projected, 'state', {get() {
    return {...ctx.state, planning: effective?.planning ?? null};
  }});
  async function readProjection() {
    const number = ++readNumber;
    if(ctx.liveRefresh)await ctx.refreshWork?.();
    const result = await api('/api/work/period/day');
    if (disposed || number < acceptedRead) return;
    acceptedRead = number;
    if (!result || !Object.hasOwn(result, 'planning') || !Number.isInteger(result.revision)) {
      throw new Error('Invalid Work day projection');
    }
    if (effective && result.revision < effective.revision) return;
    effective = result;
    signature=`${result.revision}|${result.fingerprint||JSON.stringify(result.planning)}`;
    initialError=null;
    if (!delegate) delegate = createConstructor(root, projected);
  }
  async function refresh(minimumRevision=0){
    if(inFlight){await inFlight;if(disposed||effective?.revision>=minimumRevision)return;}
    let attempts=0;
    do{
      inFlight=readProjection();
      try{await inFlight;}finally{inFlight=null;}
      if(++attempts>=3 && effective?.revision<minimumRevision)throw new Error('Work day projection is older than the saved change');
    }while(!disposed && effective?.revision<minimumRevision);
  }
  projected.action = async (...args) => {
    const result = await ctx.action(...args);
    await refresh(result?.revision||0);
    return result;
  };
  projected.refreshWork = async (...args) => {
    const result = await ctx.refreshWork?.(...args);
    await refresh();
    return result;
  };
  function schedule(method) {
    pending = refresh().then(() => {
      if (!disposed && (method==='render'||signature!==deliveredSignature)){deliveredSignature=signature;delegate?.[method]?.();}
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
  const poll=ctx.liveRefresh?setPoll(()=>{void schedule('remoteChanged');},750):null;
  poll?.unref?.();
  return {
    render() {return schedule('render');},
    remoteChanged() {return schedule('remoteChanged');},
    async ready() {await pending;if(!delegate)throw initialError||new Error(loadingText);},
    async prepareLeave() {await pending;return delegate?.prepareLeave?.();},
    destroy() {disposed = true;if(poll!==null)clearPoll(poll);delegate?.destroy();},
  };
}
