// Keep existing controls, focus, drafts and scroll positions when a projection changes.
function fieldKey(node) {
  if(node.nodeType!==1)return false;
  if(node.id)return node.id;
  const radioValue=node.type==='radio'?node.value:'';
  const {field,id,srcInput,cube,goal}=node.dataset;
  if(field)return `field:${field}:${id}:${radioValue}`;
  if(srcInput)return `source:${srcInput}`;
  const kind=node.nodeName,firstClass=node.classList[0];
  // Preserve the existing cube key, including its newline.
  if(cube)return `cube:${kind}:\n${firstClass}:${cube}`;
  if(goal)return `goal:${kind}:${firstClass}:${goal}`;
  if(node.name)return `name:${node.name}:${radioValue}`;
  return '';
}
function compatible(a,b) { return a.nodeType === b.nodeType && a.nodeName === b.nodeName && (!fieldKey(a) || fieldKey(a) === fieldKey(b)); }
function patchNode(node, next, document) {
  if(node.nodeType !== 1) { if(node.nodeValue !== next.nodeValue) node.nodeValue = next.nodeValue; return; }
  const active=node===document.activeElement;
  const selection=active&&node.selectionStart!=null?{start:node.selectionStart,end:node.selectionEnd,direction:node.selectionDirection}:null;
  const protectedField = node.dataset.workDirty === '1' || (active && !node.dataset.field);
  for(const a of [...node.attributes]) if(!next.hasAttribute(a.name) && a.name !== 'data-work-dirty' && !(protectedField && ['value','checked',
'selected'].includes(a.name))) node.removeAttribute(a.name);
  for(const a of [...next.attributes]) if(node.getAttribute(a.name) !== a.value && !(protectedField && ['value','checked',
'selected'].includes(a.name))) node.setAttribute(a.name,a.value);
  if(!protectedField && ['INPUT','TEXTAREA'].includes(node.nodeName)) {
    if(node.value !== next.value)node.value=next.value;
    if(selection)node.setSelectionRange(Math.min(selection.start,node.value.length),Math.min(selection.end,node.value.length),selection.direction);
    if(node.type === 'checkbox' || node.type === 'radio') node.checked=next.checked;
  }
  if(node.nodeName === 'SELECT' && protectedField) return;
  patchChildren(node,next,document);
  if(node.nodeName === 'SELECT' && !protectedField && node.value !== next.value) node.value=next.value;
}
function patchChildren(parent, next, document) {
  let cursor=parent.firstChild;
  const keyed = new Map([...parent.childNodes].map(n=>[fieldKey(n),n]).filter(([key])=>key));
  for(const fresh of [...next.childNodes]) {
    const key=fieldKey(fresh), found=key && keyed.get(key);
    if(found && found!==cursor && compatible(found,fresh)) parent.insertBefore(found,cursor);
    const node=found && compatible(found,fresh) ? found : cursor;
    if(node && compatible(node,fresh)) { patchNode(node,fresh,document); cursor=node.nextSibling; }
    else { parent.insertBefore(fresh,cursor); }
  }
  while(cursor) { const nextNode=cursor.nextSibling; cursor.remove(); cursor=nextNode; }
}
export function patchMarkup(host, html) {
  // A gesture can alter classes/styles without changing the authoritative HTML.
  // Walk the existing tree even for that same HTML so cancel/rejection clears its preview.
  const template=host.ownerDocument.createElement('template'); template.innerHTML=html;
  patchChildren(host,template.content,host.ownerDocument); host.__workMarkup=html; return true;
}
