// The desktop adapter supplies the same chrome API used by the extension.
// Await it before constructing the connection or loading any planner request.
await import('../desktop-platform.js');
const [{createConnection}, {configurePeriodTransport}] = await Promise.all([
  import('../connection.js'),
  import('./transport.js'),
]);
const {initI18n, syncLocale, applyDocument, localeChange} = await import('../i18n.js');
await initI18n();
if (!await syncLocale()) {
  applyDocument();
  configurePeriodTransport(createConnection(globalThis.chrome));
  const planner=await import('./app.js');
  const {installWorkViewLifecycle}=await import('../work-view-lifecycle.js');
  const lifecycle=installWorkViewLifecycle({prepareLeave:planner.prepareLeave});
  planner.onModelReady(()=>lifecycle.ready());
  try{await planner.ready();lifecycle.ready();}catch(error){lifecycle.ready(error);}
  globalThis.chrome?.storage?.onChanged?.addListener(changes => {
    if (localeChange(changes)) globalThis.location?.reload();
  });
}
