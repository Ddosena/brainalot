import {t} from '../i18n.js';

const hasCyrillic = /[А-ЯЁа-яё]/;
const escape = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

// Only source-code fragments pass through this helper. Interpolated titles,
// descriptions and other personal text are joined afterwards without translation.
export function uiMarkup(fragment, html = true) {
  return fragment.split(/([<>"'])/).map(part => {
    if (!hasCyrillic.test(part)) return part;
    const value = part.trim();
    const translated = t(value);
    return part.replace(value, html ? escape(translated) : translated);
  }).join('');
}
export function ui(parts, ...values) {
  const html = parts.some(part => /<\/?[a-z]/i.test(part));
  return parts.reduce((result, part, index) => result + uiMarkup(part, html) + (index < values.length ? values[index] : ''), '');
}
