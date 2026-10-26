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
  await import('./app.js');
  globalThis.chrome?.storage?.onChanged?.addListener(changes => {
    if (localeChange(changes)) globalThis.location?.reload();
  });
}
