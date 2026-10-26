import {t} from '../i18n.js';
// Keep credentials and service discovery in the app's common connection layer.
const ROUTES = Object.freeze({
  '/api/model': '/api/work/period/model',
  '/api/context': '/api/work/period/context',
  '/api/command': '/api/work/period/command',
  '/api/snapshot': '/api/work/period/snapshot',
});

export function createPeriodTransport(connection) {
  if (typeof connection?.api !== 'function') throw new TypeError('Work connection is required.');
  return async function request(path, options = {}) {
    if (!Object.hasOwn(ROUTES, path)) throw new TypeError('Unknown Work endpoint.');
    const method = options.method || 'GET';
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : options.body;
    try {
      const value = await connection.api(ROUTES[path], method, body, {timeoutMs:15000});
      return {ok:true, status:200, json:async () => value};
    } catch (error) {
      return {ok:false, status:error?.status || 0, json:async () => ({error:error?.message || t('Не удалось подключиться к Brainalot.')})};
    }
  };
}

let activeTransport;
export function configurePeriodTransport(connection) {
  activeTransport = createPeriodTransport(connection);
}
export function periodRequest(path, options) {
  if (!activeTransport) throw new Error(t('Подключение Work ещё не готово.'));
  return activeTransport(path, options);
}
