'use strict';
const $ = (s, root = document) => root.querySelector(s);
const state = {
  user: null,
  csrf: '',
  identities: [],
  folder: 'inbox',
  page: 1,
  pages: 1,
  messages: [],
  current: null,
  admin: null,
  tokens: null,
  demo: false,
  login_allowed: false,
  generation: 0
};
const folders = [
  ['inbox', 'Inbox', 'inbox'],
  ['sent', 'Sent', 'send'],
  ['drafts', 'Drafts', 'pen'],
  ['archive', 'Archive', 'archive'],
  ['junk', 'Junk', 'shield'],
  ['trash', 'Trash', 'trash']
];
const adminPages = [
  ['overview', 'Overview', 'shield'],
  ['users', 'People & access', 'users'],
  ['domains', 'Domains & DNS', 'globe'],
  ['tokens', 'API tokens', 'key']
];

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (k === 'text') n.textContent = v;
    else if (v !== false && v != null) n.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat())
    if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}

function icon(name) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  n.setAttribute('class', 'icon');
  n.setAttribute('aria-hidden', 'true');
  const u = document.createElementNS(n.namespaceURI, 'use');
  u.setAttribute('href', '#i-' + name);
  n.append(u);
  return n;
}

function button(text, fn, cls = 'button', sym) {
  return el('button', {
    type: 'button',
    class: cls,
    onclick: fn
  }, sym ? icon(sym) : null, text);
}

function ib(sym, label, fn) {
  const b = button('', fn, 'icon-button', sym);
  b.setAttribute('title', label);
  b.setAttribute('aria-label', label);
  return b;
}

function initials(name) {
  return name.replace(/<.*>/g, '').trim().split(/\s+/).slice(0, 2).map(x => x[0] || '').join('').toUpperCase() || 'K';
}

function senderName(value) {
  return value.includes('<') ? value.split('<')[0].replaceAll('"', '').trim() || value : value.split('@')[0];
}

function addressOf(value) {
  const m = value.match(/<([^>]+)>/);
  return m ? m[1] : value;
}

function dateText(v, full = false) {
  const d = new Date(v);
  if (!Number.isFinite(+d)) return '';
  return d.toLocaleDateString(undefined, full ? {
    month: 'short',
    day: 'numeric',
    year: 'numeric'
  } : {
    month: 'short',
    day: 'numeric'
  });
}
let toastTimer;

function toast(text, error = false) {
  const t = $('#toast');
  t.textContent = text;
  t.className = 'toast' + (error ? ' error' : '');
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.hidden = true, error ? 10000 : 6000);
}
async function api(path, options = {}, allowReauth = true) {
  const headers = {
    'X-CSRF-Token': state.csrf,
    ...options.headers
  };
  let body = options.body;
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  const r = await fetch('/api' + path, {
    ...options,
    headers,
    body
  });
  const jsonResponse = (r.headers.get('Content-Type') || '').includes('application/json');
  const fallback = r.status === 429 ? 'Too many requests. Please wait a minute and try again.' :
    r.status === 413 ? 'The upload is too large. Keep attachments at 16 MiB or less.' :
    'Server unavailable. If sending mail, check delivery before retrying.';
  const data = jsonResponse ? await r.json() : {
    error: fallback
  };
  if (r.status === 403 && data.code === 'reauth_required' && allowReauth) {
    await confirmRecentPassword();
    return api(path, options, false);
  }
  if (!r.ok || !jsonResponse) {
    if (r.status === 401 && path !== '/login' && path !== '/session') {
      await boot();
    }
    throw new Error(data.error || 'Request failed.');
  }
  return data;
}
let reauthPending;

function confirmRecentPassword() {
  if (reauthPending) return reauthPending;
  reauthPending = new Promise((resolve, reject) => {
    const dialog = $('#reauth-dialog');
    const form = $('#reauth-form');
    const submit = $('button[type=submit]', form);
    let confirmed = false;
    form.reset();
    submit.disabled = false;
    $('#reauth-error').textContent = '';
    async function onSubmit(event) {
      event.preventDefault();
      submit.disabled = true;
      $('#reauth-error').textContent = '';
      try {
        await api('/account/reauth', {
          method: 'POST',
          body: {
            password: form.elements.password.value
          }
        }, false);
        confirmed = true;
        dialog.close();
      } catch (error) {
        $('#reauth-error').textContent = error.message;
      } finally {
        submit.disabled = false;
      }
    }
    form.addEventListener('submit', onSubmit);
    dialog.addEventListener('close', () => {
      form.removeEventListener('submit', onSubmit);
      form.reset();
      reauthPending = null;
      if (confirmed) resolve();
      else reject(new Error('Change canceled.'));
    }, {
      once: true
    });
    dialog.showModal();
    form.elements.password.focus();
  });
  return reauthPending;
}

async function busy(b, fn) {
  b.disabled = true;
  try {
    await fn();
  } catch (e) {
    toast(e.message, true);
  } finally {
    b.disabled = false;
  }
}

function blank(title, description, sym = 'mail') {
  return el('div', {
    class: 'empty-state'
  }, el('div', {
    class: 'empty-art'
  }, icon(sym)), el('h2', {}, title), description ? el('p', {}, description) : null);
}

function heading(title) {
  $('#page-title').textContent = title;
  $('#location-page').textContent = title;
  $('#location-group').textContent = state.folder === location.hash.slice(1) || !location.hash ? 'Mailbox' : location.hash === '#account' ? 'Settings' : 'Administration';
}

function nav() {
  $('#mail-nav').replaceChildren(...folders.map(([id, label, sym]) => el('a', {
    href: '#' + id,
    class: 'nav-item',
    'data-page': id
  }, icon(sym), label)));
  $('#admin-nav').replaceChildren(...adminPages.map(([id, label, sym]) => el('a', {
    href: '#' + id,
    class: 'nav-item',
    'data-page': id
  }, icon(sym), label)));
  $('#admin-nav-wrap').hidden = !state.user.admin;
  $('#sidebar-name').textContent = state.user.name;
  $('#sidebar-avatar').textContent = initials(state.user.name);
  $('#sidebar-role').textContent = state.user.admin ? 'Administrator' : 'Personal mailbox';
}
function renderLoginAccess(session) {
  const allowed = session.login_allowed === true;
  const form = $('#login-form');
  form.hidden = !allowed;
  for (const control of form.elements) control.disabled = !allowed;
  $('#login-help').hidden = !allowed;
  $('#login-access').hidden = allowed;
  $('#secure-login-link').hidden = true;
  $('#secure-login-link').removeAttribute('href');
  $('#login-title').textContent = 'Sign in';
  if (allowed) return;
  form.reset();
  let destination;
  try {
    destination = new URL(session.login_url);
    if (destination.protocol !== 'https:' || destination.username || destination.password) destination = null;
  } catch {}
  const alreadyCanonical = destination?.origin === location.origin;
  $('#login-title').textContent = alreadyCanonical ? 'Secure sign-in unavailable' : 'Open secure mail';
  $('#login-access-message').textContent = alreadyCanonical
    ? 'The server could not confirm the secure connection. Ask your administrator to check the mail proxy’s HTTPS settings.'
    : destination
      ? `Sign in at ${destination.hostname} using HTTPS. This address cannot keep your account securely signed in. If the secure site does not open, ask your administrator to finish its setup.`
      : 'The secure mail address is unavailable. Ask your administrator to check the mail website configuration.';
  if (destination && !alreadyCanonical) {
    $('#secure-login-link').href = destination.href;
    $('#secure-login-link').hidden = false;
  }
}

async function boot() {
  try {
    const s = await api('/session');
    Object.assign(state, s);
    $('#loading').hidden = true;
    $('#workspace').hidden = !s.user;
    $('#login-view').hidden = !!s.user;
    $('#demo-login').hidden = !s.demo;
    $('#preview-badge').hidden = !s.demo;
    $('#server-host').textContent = s.hostname || '';
    renderLoginAccess(s);
    if (s.user) {
      nav();
      await route();
    } else {
      state.messages = [];
      state.current = null;
      state.admin = null;
      state.tokens = null;
      state.generation++;
      for (const id of ['message-list', 'message-reader', 'admin-view', 'account-view']) document.getElementById(id).replaceChildren();
      $('#compose-form').reset();
      document.querySelectorAll('dialog[open]').forEach(d => d.close());
      document.title = 'Sign in · KagNode Mail';
      if (s.login_allowed) $('#login-form [name=email]').focus();
      else $('#login-access').focus();
    }
  } catch (e) {
    $('#loading').replaceChildren(blank('Could not open KagNode Mail.', e.message), button('Try again', () => boot()));
  }
}
async function route() {
  if (!state.user) return;
  const page = location.hash.slice(1) || 'inbox';
  if (page !== 'tokens' && $('#token-secret')) $('#form-dialog').close();
  setSidebar(false);
  document.querySelectorAll('[data-page]').forEach(n => {
    const active = n.dataset.page === page;
    n.classList.toggle('active', active);
    if (active) n.setAttribute('aria-current', 'page');
    else n.removeAttribute('aria-current');
  });
  $('.sidebar-navigation [aria-current=page]')?.scrollIntoView({block: 'nearest'});
  $('#mail-view').hidden = !folders.some(f => f[0] === page);
  $('#admin-view').hidden = !adminPages.some(f => f[0] === page);
  $('#account-view').hidden = page !== 'account';
  document.title = (folders.find(f => f[0] === page)?.[1] || adminPages.find(f => f[0] === page)?.[1] || 'Account') + ' · KagNode Mail';
  if (folders.some(f => f[0] === page)) {
    state.folder = page;
    state.page = 1;
    state.current = null;
    $('#mail-search').value = '';
    $('#mail-view').classList.remove('reading');
    heading(folders.find(f => f[0] === page)[1]);
    $('#message-reader').replaceChildren(blank('Select a message'));
    await loadMessages();
  } else if (page === 'account') {
    renderAccount();
  } else if (state.user.admin && adminPages.some(f => f[0] === page)) {
    heading({
      overview: 'Overview',
      users: 'People & access',
      domains: 'Domains & DNS',
      tokens: 'API tokens'
    } [page]);
    $('#admin-view').replaceChildren(blank('Loading…'));
    try {
      if (page === 'tokens') {
        const data = await api('/admin/tokens');
        if (!state.user?.admin || location.hash !== '#tokens') return;
        state.tokens = data;
        renderTokens();
      } else {
        const data = await api('/admin');
        if (!state.user?.admin || location.hash.slice(1) !== page) return;
        state.admin = data;
        renderAdmin(page);
      }
    } catch (e) {
      $('#admin-view').replaceChildren(blank('Could not load settings.', e.message));
    }
  } else {
    location.hash = 'inbox';
  }
}
async function loadMessages() {
  const gen = ++state.generation;
  $('#message-list').replaceChildren(blank('Loading…'));
  try {
    const r = await api('/messages?' + new URLSearchParams({
      folder: state.folder,
      page: state.page,
      q: $('#mail-search').value
    }));
    if (gen !== state.generation) return;
    state.messages = r.items;
    state.pages = r.pages;
    $('#list-count').textContent = r.total + ' message' + (r.total === 1 ? '' : 's');
    $('#page-count').textContent = `${state.page} of ${r.pages}`;
    $('#prev-page').disabled = state.page <= 1;
    $('#next-page').disabled = state.page >= r.pages;
    renderList();
  } catch (e) {
    if (gen === state.generation) {
      state.messages = [];
      $('#list-count').textContent = 'Unable to load';
      $('#page-count').textContent = '';
      $('#prev-page').disabled = true;
      $('#next-page').disabled = true;
      const notice = blank('Could not load your mailbox.', e.message, 'refresh');
      notice.append(button('Try again', () => loadMessages(), 'button', 'refresh'));
      $('#message-list').replaceChildren(notice);
    }
  }
}

function renderList() {
  const nodes = state.messages.map(m => {
    const n = el('button', {
      class: 'message-row' + (m.unread ? ' unread' : '') + (state.current?.uid === m.uid ? ' selected' : ''),
      onclick: () => openMessage(m.uid)
    }, m.unread ? el('span', {
      class: 'unread-dot'
    }) : null, el('span', {
      class: 'row-top'
    }, el('span', {
      class: 'sender-name'
    }, senderName(state.folder === 'sent' ? m.to : m.sender)), el('span', {
      class: 'row-date'
    }, dateText(m.date))), el('span', {
      class: 'row-subject'
    }, m.subject || '(No subject)'), el('span', {
      class: 'row-preview'
    }, m.preview || m.sender));
    if (m.starred) n.append(el('span', {
      class: 'row-bottom'
    }, icon('star'), el('span', {
      class: 'tag'
    }, 'Starred')));
    return n;
  });
  $('#message-list').replaceChildren(...(nodes.length ? nodes : [blank('No messages')]));
}
async function openMessage(uid) {
  const folder = state.folder;
  state.current = {
    uid
  };
  renderList();
  $('#mail-view').classList.add('reading');
  $('#message-reader').replaceChildren(blank('Loading…'));
  try {
    const m = await api(`/messages/${uid}?folder=${encodeURIComponent(folder)}`);
    if (state.folder !== folder || state.current?.uid !== uid) return;
    state.current = m;
    const item = state.messages.find(x => x.uid === uid);
    if (item) item.unread = false;
    renderList();
    renderReader(m);
  } catch (e) {
    $('#message-reader').replaceChildren(blank('Could not open this message.', e.message));
  }
}

function renderReader(m) {
  const back = ib('chevron', 'Back to messages', () => $('#mail-view').classList.remove('reading'));
  back.classList.add('reader-back');
  const item = state.messages.find(x => x.uid === m.uid);
  const toolbar = el('div', {
    class: 'reader-toolbar'
  }, back, ib('archive', 'Archive message', () => mailAction('archive')), ib('trash', state.folder === 'trash' ? 'Delete permanently' : 'Move to trash', () => state.folder === 'trash' ? formDialog('Delete this message?', [el('p', {
    class: 'panel-note'
  }, 'This cannot be undone.')], 'Delete message', async () => mailAction('delete')) : mailAction('trash')), el('span', {
    class: 'toolbar-divider'
  }), ib('mail', 'Mark unread', () => mailAction('unread')), ib('star', item?.starred ? 'Remove star' : 'Star message', () => mailAction(item?.starred ? 'unstar' : 'star')), ib('shield', 'Move to junk', () => mailAction('junk')));
  const content = el('div', {
    class: 'reader-content'
  }, el('h2', {}, m.subject || '(No subject)'), el('div', {
    class: 'sender-block'
  }, el('span', {
    class: 'avatar'
  }, initials(senderName(m.sender))), el('div', {
    class: 'sender-details'
  }, el('strong', {
    title: m.sender
  }, senderName(m.sender)), el('small', {}, addressOf(m.sender)), el('small', {}, 'To ' + m.to + (m.cc ? ' · Cc ' + m.cc : ''))), el('span', {
    class: 'sender-date'
  }, dateText(m.date, true))), el('pre', {
    class: 'message-body'
  }, m.body));
  const attachments = el('div', {
    class: 'reader-attachments'
  });
  for (const a of m.attachments || []) attachments.append(el('a', {
    class: 'attachment',
    href: `/api/messages/${m.uid}/attachments/${a.id}?folder=${encodeURIComponent(state.folder)}`
  }, icon('paperclip'), a.name));
  content.append(attachments, el('div', {
      class: 'reply-bar'
    }, button(state.folder === 'drafts' ? 'Continue writing' : 'Reply', () => compose(state.folder === 'drafts' ? {
      to: m.to,
      cc: m.cc,
      bcc: m.bcc || '',
      subject: m.subject,
      body: m.body,
      draft_uid: m.uid
    } : {
      to: addressOf(state.folder === 'sent' ? m.to : m.sender),
      subject: /^re:/i.test(m.subject) ? m.subject : 'Re: ' + m.subject,
      body: '\n\nOn ' + dateText(m.date, true) + ', ' + m.sender + ' wrote:\n' + m.body.split('\n').map(l => '> ' + l).join('\n')
    }, state.folder === 'drafts' ? m.attachments : []), 'button', 'reply'), button('Forward', () => compose({
      subject: 'Fwd: ' + m.subject,
      body: '\n\n---------- Forwarded message ----------\nFrom: ' + m.sender + '\nSubject: ' + m.subject + '\n\n' + m.body
    }, m.attachments), 'button', 'send')),
    el('div', {
      class: 'reader-bottom'
    }, icon('shield'), 'Remote content blocked.', el('a', {
      href: `/api/messages/${m.uid}/attachments/original?folder=${encodeURIComponent(state.folder)}`,
      class: 'text-button'
    }, 'Download original')));
  $('#message-reader').replaceChildren(toolbar, content);
}
async function mailAction(action) {
  if (!state.current) return;
  try {
    await api(`/messages/${state.current.uid}/action`, {
      method: 'POST',
      body: {
        folder: state.folder,
        action
      }
    });
    toast('Updated.');
    const uid = state.current.uid;
    await loadMessages();
    if (['read', 'unread', 'star', 'unstar'].includes(action)) {
      const m = state.messages.find(x => x.uid === uid);
      if (m && action.includes('star')) renderReader(state.current);
    } else {
      state.current = null;
      $('#message-reader').replaceChildren(blank('Select a message'));
      $('#mail-view').classList.remove('reading');
    }
  } catch (e) {
    toast(e.message, true);
  }
}
let composeGeneration = 0;
async function compose(data = {}, attachments = []) {
  setSidebar(false);
  const generation = ++composeGeneration,
    sourceUID = state.current?.uid,
    sourceFolder = state.folder;
  const f = $('#compose-form');
  f.reset();
  $('#compose-error').textContent = '';
  $('#compose-from').replaceChildren(...state.identities.map(v => el('option', {
    value: v
  }, v)));
  $('#compose-from').value = state.user.email;
  for (const [k, v] of Object.entries(data))
    if (f.elements[k]) f.elements[k].value = v;
  $('#compose-dialog').showModal();
  f.elements[data.to ? 'body' : 'to'].focus();
  const submit = $('button[type=submit]', f),
    save = $('#save-draft');
  if (attachments.length) {
    submit.disabled = true;
    save.disabled = true;
    try {
      const transfer = new DataTransfer();
      for (const a of attachments) {
        const r = await fetch(`/api/messages/${sourceUID}/attachments/${a.id}?folder=${encodeURIComponent(sourceFolder)}`);
        if (!r.ok) throw new Error('Could not load existing attachments. Close this composer and try again.');
        transfer.items.add(new File([await r.blob()], a.name, {
          type: 'application/octet-stream'
        }));
      }
      if (generation !== composeGeneration || !$('#compose-dialog').open) return;
      f.elements.attachments.files = transfer.files;
      submit.disabled = false;
      save.disabled = false;
    } catch (e) {
      if (generation === composeGeneration) $('#compose-error').textContent = e.message;
    }
  } else {
    submit.disabled = false;
    save.disabled = false;
  }
}

function dialog(title, content) {
  $('#form-dialog-content').replaceChildren(el('header', {
    class: 'dialog-header'
  }, el('h2', {}, title), ib('close', 'Close dialog', () => $('#form-dialog').close())), content);
  $('#form-dialog').showModal();
}

function field(label, name, type = 'text', value = '', extra = {}) {
  return el('label', {}, label, el('input', {
    name,
    type,
    value,
    ...extra
  }));
}

function formDialog(title, fields, submitLabel, onSubmit) {
  const f = el('form', {
    class: 'stack-form'
  }, ...fields);
  const err = el('p', {
    class: 'form-error',
    role: 'alert'
  });
  const b = el('button', {
    type: 'submit',
    class: 'button primary'
  }, submitLabel);
  f.append(err, b);
  f.addEventListener('submit', async ev => {
    ev.preventDefault();
    b.disabled = true;
    try {
      await onSubmit(new FormData(f), f);
      $('#form-dialog').close();
      await route();
      toast('Saved.');
    } catch (e) {
      err.textContent = e.message;
    } finally {
      b.disabled = false;
    }
  });
  dialog(title, f);
}

function panel(title, subtitle, action, ...body) {
  return el('div', {
    class: 'panel'
  }, el('div', {
    class: 'panel-heading'
  }, el('div', {}, el('h2', {}, title), subtitle ? el('p', {}, subtitle) : null), action), ...body);
}

function table(headers, rows) {
  return el('div', {
    class: 'table-wrap'
  }, el('table', {}, el('thead', {}, el('tr', {}, ...headers.map(h => el('th', {}, h)))), el('tbody', {}, ...rows)));
}

function renderAdmin(page) {
  const a = state.admin,
    root = $('#admin-view');
  root.replaceChildren();
  if (page === 'overview') {
    root.append(el('div', {
      class: 'stat-grid'
    }, ...[
      [a.users.filter(u => u.active).length, 'Active mailboxes', 'users'],
      [a.domains.length, 'Hosted domains', 'globe'],
      [a.aliases.length, 'Email aliases', 'mail']
    ].map(([n, label, sym]) => el('div', {
      class: 'stat-card'
    }, icon(sym), el('strong', {}, n), el('span', {}, label)))));
    root.append(panel('Activity', null, null, table(['WHEN', 'WHO', 'ACTION', 'TARGET'], a.audit.map(r => el('tr', {}, el('td', {}, r.created), el('td', {}, r.actor), el('td', {}, r.action), el('td', {}, r.target))))));
  } else if (page === 'users') {
    root.append(panel('Accounts', a.users.length + ' accounts', button('Add account', createUser, 'button primary', 'plus'), table(['PERSON', 'ROLE', 'QUOTA', 'STATUS', ''], a.users.map(u => el('tr', {}, el('td', {}, el('strong', {}, u.name), el('small', {}, u.email)), el('td', {}, el('span', {
      class: 'badge' + (u.admin ? '' : ' neutral')
    }, u.admin ? 'Admin' : 'Member')), el('td', {}, u.quota_mb + ' MiB'), el('td', {}, el('span', {
      class: 'badge' + (u.active ? '' : ' orange')
    }, u.active ? 'Active' : 'Disabled')), el('td', {}, el('div', {
      class: 'inline-actions'
    }, button('Edit', () => editUser(u), 'text-button'), button('Send as', () => editIdentities(u), 'text-button'))))))));
    root.append(panel('Aliases', 'Receive mail at another address. Grant send-as access separately.', button('Add alias', createAlias, 'button', 'plus'), a.aliases.length ? table(['ADDRESS', 'DELIVERS TO', ''], a.aliases.map(r => el('tr', {}, el('td', {}, r.email), el('td', {}, r.destination), el('td', {}, button('Remove', () => removeAlias(r), 'text-button'))))) : el('p', {
      class: 'panel-note'
    }, 'No aliases.')));
  } else {
    root.append(panel('Domains', null, button('Add domain', () => formDialog('Add a domain', [field('Domain name', 'name', 'text', '', {
      placeholder: 'example.com',
      required: true
    })], 'Add domain', async d => api('/admin/domains', {
      method: 'POST',
      body: {
        name: d.get('name')
      }
    })), 'button primary', 'plus'), ...a.domains.map(name => el('div', {
      class: 'domain-row'
    }, el('div', {
      class: 'domain-name'
    }, icon('globe'), el('div', {}, el('strong', {}, name), el('small', {}, a.hostname))), button('DNS setup', () => showDNS(name), 'button', 'settings')))));
    root.append(panel('Server address', a.hostname + ' · ' + a.ipv4, button('Edit public IP', () => formDialog('Public IPv4 address', [field('Public IPv4', 'ipv4', 'text', a.ipv4, {
      required: true
    }), el('p', {
      class: 'panel-note'
    }, 'Updates these instructions only. Change A and PTR records with your providers; server networking is unchanged.')], 'Save address', async d => api('/admin/network', {
      method: 'PUT',
      body: {
        ipv4: d.get('ipv4')
      }
    })), 'button', 'settings')));
    root.append(panel('Provider setup', null, null, el('p', {
      class: 'panel-note'
    }, 'Point the mail hostname to your public IP. Publish the MX and TXT records from DNS setup. Set PTR with your hosting provider. Allow TCP 25, 80, 443, 587 and 993; outbound 25 is required.'), el('div', {
      class: 'notice'
    }, 'Disable DNS proxying for the mail hostname. Publish IPv6 only after outbound IPv6 and PTR work.')));
  }
}

const tokenScopes = [
  ['read', 'Read'], ['send', 'Send'], ['write', 'Write'], ['delete', 'Permanent delete']
];

function tokenDate(value, fallback) {
  return value ? dateText(new Date(value * 1000), true) : fallback;
}

function renderTokens() {
  const tokens = state.tokens.tokens;
  const cards = tokens.map(t => {
    const expired = t.expires && t.expires <= Date.now() / 1000;
    const inactive = t.revoked || expired;
    return el('article', {
      class: 'token-card', 'data-token-id': t.id
    }, el('div', { class: 'token-card-heading' }, el('div', {},
      el('h3', {}, t.label), el('p', {}, t.email)),
      el('span', { class: 'badge' + (inactive ? ' neutral' : '') },
        t.revoked ? 'Revoked' : expired ? 'Expired' : 'Active')),
    el('div', { class: 'token-scopes' }, ...tokenScopes.filter(([id]) => t.scopes.includes(id))
      .map(([, label]) => el('span', { class: 'badge neutral' }, label))),
    t.senders.length ? el('p', { class: 'token-senders' }, 'Send as: ' + t.senders.join(', ')) : null,
    el('dl', { class: 'token-details' },
      el('div', {}, el('dt', {}, 'Expires'), el('dd', {}, tokenDate(t.expires, 'Never'))),
      el('div', {}, el('dt', {}, 'Last used'), el('dd', {}, tokenDate(t.last_used, 'Never')))),
    el('div', { class: 'inline-actions token-actions' },
      button('Replace', () => createToken(t), 'text-button'),
      !t.revoked ? button('Revoke', () => revokeToken(t), 'text-button') : null));
  });
  $('#admin-view').replaceChildren(panel(tokens.length + (tokens.length === 1 ? ' token' : ' tokens'), null,
    button('Create token', () => createToken(), 'button primary', 'plus'),
    cards.length ? el('div', { class: 'token-grid' }, ...cards) :
      el('p', { class: 'panel-note' }, 'No API tokens.')));
}

function revokeToken(token) {
  formDialog('Revoke ' + token.label + '?', [el('p', { class: 'panel-note' },
    'Stops this token immediately.')], 'Revoke token', async () => {
    await api('/admin/tokens/' + encodeURIComponent(token.id), { method: 'DELETE' });
  });
}

function createToken(previous = null) {
  const users = state.tokens.users.filter(u => u.active);
  if (!users.length) {
    toast('Add an active mailbox first.', true);
    return;
  }
  const userSelect = el('select', { name: 'user_id', required: true },
    ...users.map(u => el('option', { value: u.id }, u.email)));
  if (previous && !users.some(u => u.id === previous.user_id)) {
    toast('Enable this mailbox before replacing its token.', true);
    return;
  }
  if (previous) userSelect.value = String(previous.user_id);
  const permissions = el('fieldset', { class: 'token-fieldset' }, el('legend', {}, 'Permissions'),
    el('div', { class: 'token-scope-options' }, ...tokenScopes.map(([id, label]) =>
      el('label', { class: 'checkbox-label' }, el('input', {
        type: 'checkbox', name: 'scopes', value: id, checked: previous?.scopes.includes(id)
      }), label))));
  const senders = el('fieldset', { class: 'token-fieldset', hidden: true },
    el('legend', {}, 'Send as'));
  const senderOptions = el('div', { class: 'identity-list' });
  senders.append(senderOptions);
  const expiry = el('select', { name: 'expiry' },
    el('option', { value: '90' }, '90 days'),
    el('option', { value: 'custom' }, 'Custom'),
    el('option', { value: 'never' }, 'Never'));
  const days = field('Days', 'expires_days', 'number', '90', { min: 1, max: 365, required: true });
  days.hidden = true;
  $('input', days).disabled = true;
  expiry.addEventListener('change', () => {
    days.hidden = expiry.value !== 'custom';
    $('input', days).disabled = days.hidden;
  });
  const form = el('form', { class: 'stack-form', id: 'token-form' },
    field('Label', 'label', 'text', previous?.label || '', { required: true, maxlength: 80, autocomplete: 'off' }),
    el('label', {}, 'Mailbox', userSelect), permissions,
    el('p', { class: 'panel-note' }, 'Write includes Trash. Permanent deletion needs its own permission.'),
    senders, el('label', {}, 'Expires', expiry), days);
  const error = el('p', { class: 'form-error', role: 'alert' });
  const submit = el('button', { type: 'submit', class: 'button primary' }, 'Create token');
  form.append(error, submit);
  function updateSenders(initial = false) {
    const u = users.find(u => String(u.id) === userSelect.value);
    senderOptions.replaceChildren(...(u?.identities || []).map(address =>
      el('label', { class: 'checkbox-label' }, el('input', {
        type: 'checkbox', name: 'senders', value: address,
        checked: initial && previous?.senders.includes(address)
      }), address)));
  }
  function updateScopeFields() {
    senders.hidden = !form.querySelector('[name=scopes][value=send]').checked;
    senders.disabled = senders.hidden;
  }
  userSelect.addEventListener('change', () => updateSenders());
  permissions.addEventListener('change', updateScopeFields);
  updateSenders(true);
  updateScopeFields();
  form.addEventListener('submit', async ev => {
    ev.preventDefault();
    error.textContent = '';
    const data = new FormData(form);
    const scopes = data.getAll('scopes');
    if (!scopes.length) {
      error.textContent = 'Select at least one permission.';
      return;
    }
    if (scopes.includes('send') && !data.getAll('senders').length) {
      error.textContent = 'Select at least one sender address.';
      return;
    }
    submit.disabled = true;
    const dialogElement = $('#form-dialog');
    const closeButton = $('#form-dialog button[aria-label="Close dialog"]');
    closeButton.disabled = true;
    const preventClose = event => event.preventDefault();
    dialogElement.addEventListener('cancel', preventClose);
    try {
      const result = await api('/admin/tokens', { method: 'POST', body: {
        label: data.get('label'), user_id: Number(data.get('user_id')), scopes,
        senders: scopes.includes('send') ? data.getAll('senders') : [],
        expires_days: expiry.value === 'never' ? null : expiry.value === 'custom' ? Number(data.get('expires_days')) : 90
      } });
      if (state.user?.admin) showTokenSecret(result, previous);
    } catch (e) {
      error.textContent = e.message;
    } finally {
      dialogElement.removeEventListener('cancel', preventClose);
      closeButton.disabled = false;
      submit.disabled = false;
    }
  });
  dialog(previous ? 'Replace token' : 'Create token', form);
}

function showTokenSecret(result, previous) {
  const secret = el('input', { id: 'token-secret', type: 'text', readonly: true,
    autocomplete: 'off', spellcheck: 'false', 'aria-label': 'API token' });
  secret.value = result.secret;
  result.secret = '';
  const close = () => $('#form-dialog').close();
  const content = el('div', { class: 'dialog-body stack-form token-secret-view' },
    el('p', { class: 'panel-note' }, 'Copy now. This token will not be shown again.'),
    el('label', {}, 'API token', secret),
    button('Copy token', async () => {
      try {
        await navigator.clipboard.writeText(secret.value);
        toast('Copied.');
      } catch {
        secret.focus();
        secret.select();
        toast('Copy the selected token.', true);
      }
    }, 'button', 'copy'));
  if (previous && !previous.revoked && (!previous.expires || previous.expires > Date.now() / 1000)) {
    const status = el('p', { class: 'panel-note' }, 'The old token still works. Revoke it after updating your integration.');
    const revoke = button('Revoke old token', ev => busy(ev.currentTarget, async () => {
      await api('/admin/tokens/' + encodeURIComponent(previous.id), { method: 'DELETE' });
      status.textContent = 'Old token revoked.';
      revoke.remove();
    }), 'button');
    content.append(status, revoke);
  }
  content.append(button('Done', close, 'button primary'));
  dialog('Token created', content);
  const dialogElement = $('#form-dialog');
  function clearSecret() {
    secret.value = '';
    content.replaceChildren();
    window.removeEventListener('pagehide', clearSecret);
  }
  window.addEventListener('pagehide', clearSecret, { once: true });
  dialogElement.addEventListener('close', () => {
    clearSecret();
    if (state.user?.admin && location.hash === '#tokens') route();
  }, { once: true });
}

function createUser() {
  formDialog('Add account', [field('Display name', 'name', 'text', '', {
    required: true,
    maxlength: 100
  }), field('Email address', 'email', 'email', '', {
    required: true,
    placeholder: 'sam@' + state.admin.domains[0]
  }), field('Password · 12+ characters', 'password', 'password', '', {
    required: true,
    minlength: 12,
    maxlength: 256,
    autocomplete: 'new-password'
  }), field('Mailbox quota (MiB)', 'quota_mb', 'number', '1024', {
    min: 64,
    max: 1048576,
    required: true
  }), el('label', {
    class: 'checkbox-label'
  }, el('input', {
    type: 'checkbox',
    name: 'admin'
  }), 'Administrator access')], 'Create account', async (d) => api('/admin/users', {
    method: 'POST',
    body: {
      ...Object.fromEntries(d),
      quota_mb: Number(d.get('quota_mb')),
      admin: d.has('admin')
    }
  }));
}

function editUser(u) {
  formDialog('Edit ' + u.name, [field('Display name', 'name', 'text', u.name, {
    required: true,
    maxlength: 100
  }), field('Mailbox quota (MiB)', 'quota_mb', 'number', u.quota_mb, {
    min: 64,
    max: 1048576,
    required: true
  }), field('New password · optional', 'password', 'password', '', {
    minlength: 12,
    maxlength: 256,
    autocomplete: 'new-password'
  }), el('label', {
    class: 'checkbox-label'
  }, el('input', {
    type: 'checkbox',
    name: 'admin',
    checked: !!u.admin
  }), 'Administrator'), el('label', {
    class: 'checkbox-label'
  }, el('input', {
    type: 'checkbox',
    name: 'active',
    checked: !!u.active
  }), 'Active'), el('p', {
    class: 'panel-note'
  }, 'Saving ends web sessions. Password resets and disabling also revoke API tokens.')], 'Save changes', async d => {
    await api('/admin/users/' + u.id, {
      method: 'PATCH',
      body: {
        ...Object.fromEntries(d),
        quota_mb: Number(d.get('quota_mb')),
        active: d.has('active'),
        admin: d.has('admin')
      }
    });
    if (u.id === state.user.id) await boot();
  });
}

function editIdentities(u) {
  const choices = [...new Set([...state.admin.users.map(x => x.email), ...state.admin.aliases.map(x => x.email)])].filter(x => x !== u.email).sort();
  formDialog('Send-as permissions', [el('p', {
    class: 'panel-note'
  }, u.email + ' is always allowed. Select additional addresses for webmail and SMTP.'), el('div', {
    class: 'identity-list'
  }, ...(choices.length ? choices.map(v => el('label', {
    class: 'checkbox-label'
  }, el('input', {
    type: 'checkbox',
    name: 'identities',
    value: v,
    checked: u.identities.includes(v)
  }), v)) : [el('p', {
    class: 'panel-note'
  }, 'Add a mailbox or alias first.')]))], 'Save permissions', async d => {
    await api('/admin/users/' + u.id + '/identities', {
      method: 'PUT',
      body: {
        identities: d.getAll('identities')
      }
    });
    if (u.id === state.user.id) {
      const s = await api('/session');
      state.identities = s.identities;
    }
  });
}

function createAlias() {
  formDialog('Add alias', [field('Alias email address', 'email', 'email', '', {
    required: true,
    placeholder: 'hello@' + state.admin.domains[0]
  }), el('label', {}, 'Deliver to', el('select', {
    name: 'destination'
  }, ...state.admin.users.map(u => el('option', {
    value: u.email
  }, u.email))))], 'Create alias', async d => api('/admin/aliases', {
    method: 'POST',
    body: Object.fromEntries(d)
  }));
}

function removeAlias(r) {
  formDialog('Remove ' + r.email + '?', [el('p', {
    class: 'panel-note'
  }, 'Stops delivery and removes send-as grants for this alias. Existing mail is preserved.')], 'Remove alias', async () => api('/admin/aliases/' + encodeURIComponent(r.email), {
    method: 'DELETE'
  }));
}
async function showDNS(name, check = false) {
  try {
    const data = await api('/admin/dns/' + encodeURIComponent(name) + (check ? '?check=1' : ''));
    const body = el('div', {
      class: 'dialog-body'
    }, el('p', {
      class: 'panel-note'
    }, 'Add these records at your DNS provider. Omit the domain suffix if your provider adds it automatically.'), button(check ? 'Check again' : 'Check published DNS', ev => busy(ev.currentTarget, () => showDNS(name, true)), 'button', 'refresh'));
    body.append(el('div', {
      class: 'notice'
    }, 'DNS updates may take time. Matching records do not guarantee delivery.'));
    for (const r of data.records) {
      body.append(el('div', {
        class: 'dns-record'
      }, el('div', {
        class: 'dns-record-head'
      }, el('span', {
        class: 'badge'
      }, r.type), el('strong', {}, r.host), r.status ? el('span', {
        class: 'badge' + (r.status === 'matched' ? '' : ' orange')
      }, r.status) : null, ib('copy', 'Copy record value', async () => {
        try {
          await navigator.clipboard.writeText(r.value);
          toast('Copied.');
        } catch {
          toast('Clipboard unavailable. Copy the value manually.', true);
        }
      })), el('code', {}, r.value || 'Set the server’s public IP during installation.'), el('p', {}, r.note), r.observed?.length ? el('p', {}, 'Observed: ' + r.observed.join(' · ')) : null));
    }
    dialog('DNS setup · ' + name, body);
  } catch (e) {
    toast(e.message, true);
  }
}

function renderAccount() {
  heading('Account');
  const u = state.user;
  const f = el('form', {
    class: 'stack-form'
  }, field('Current password', 'current', 'password', '', {
    required: true,
    autocomplete: 'current-password'
  }), field('New password · 12+ characters', 'password', 'password', '', {
    required: true,
    minlength: 12,
    maxlength: 256,
    autocomplete: 'new-password'
  }), el('button', {
    type: 'submit',
    class: 'button primary'
  }, 'Update password'));
  f.addEventListener('submit', ev => {
    ev.preventDefault();
    busy($('button', f), async () => {
      await api('/account/password', {
        method: 'POST',
        body: Object.fromEntries(new FormData(f))
      });
      toast('Password updated. Sign in with your new password.');
      await boot();
    });
  });
  $('#account-view').replaceChildren(el('div', {
    class: 'settings-grid'
  }, panel('Profile', null, null, el('p', {}, u.name), el('p', {
    class: 'panel-note'
  }, u.email), el('span', {
    class: 'badge'
  }, u.admin ? 'Administrator' : 'Member'), el('p', {
    class: 'panel-note'
  }, 'Mailbox quota: ' + u.quota_mb + ' MiB'), el('h3', {}, 'Send as'), ...state.identities.map(v => el('p', {
    class: 'panel-note'
  }, v))), panel('Password', 'Changing your password ends web sessions and revokes API tokens.', null, f)), panel('Mail clients', 'Sign in with your full email address and password.', null, table(['SERVICE', 'SERVER', 'PORT', 'SECURITY'], [el('tr', {}, el('td', {}, 'IMAP'), el('td', {}, state.hostname), el('td', {}, '993'), el('td', {}, 'SSL/TLS')), el('tr', {}, el('td', {}, 'SMTP'), el('td', {}, state.hostname), el('td', {}, '587'), el('td', {}, 'STARTTLS · authentication required'))])));
}
$('#login-form').addEventListener('submit', ev => {
  ev.preventDefault();
  if (state.login_allowed !== true) return;
  const f = ev.currentTarget;
  $('#login-error').textContent = '';
  const b = $('button', f);
  b.disabled = true;
  api('/login', {
    method: 'POST',
    body: Object.fromEntries(new FormData(f))
  }).then(async () => {
    f.elements.password.value = '';
    await boot();
  }).catch(e => $('#login-error').textContent = e.message).finally(() => b.disabled = false);
});
$('#logout-button').addEventListener('click', () => busy($('#logout-button'), async () => {
  await api('/logout', {
    method: 'POST'
  });
  await boot();
}));
$('#compose-button').addEventListener('click', () => compose());
$('#compose-mobile')?.addEventListener('click', () => compose());
$('#compose-form').addEventListener('submit', async ev => {
  ev.preventDefault();
  const f = ev.currentTarget,
    b = $('button[type=submit]', f);
  $('#compose-error').textContent = '';
  b.disabled = true;
  try {
    const r = await api('/send', {
      method: 'POST',
      body: new FormData(f)
    });
    $('#compose-dialog').close();
    const draft = f.elements.draft_uid.value;
    f.reset();
    if (draft && !r.refused?.length) {
      try {
        await api(`/messages/${draft}/action`, {
          method: 'POST',
          body: {
            folder: 'drafts',
            action: 'trash'
          }
        });
      } catch {
        r.warning = 'Mail accepted, but the old draft remains. Do not resend it.';
      }
    }
    toast(r.warning || (r.refused?.length ? 'Accepted for some recipients. Retry only these rejected addresses: ' + r.refused.join(', ') : 'Message accepted for delivery.'), !!r.warning || !!r.refused?.length);
    if (state.folder === 'sent') await loadMessages();
  } catch (e) {
    $('#compose-error').textContent = e.message;
  } finally {
    b.disabled = false;
  }
});
$('#save-draft').addEventListener('click', () => busy($('#save-draft'), async () => {
  await api('/drafts', {
    method: 'POST',
    body: new FormData($('#compose-form'))
  });
  $('#compose-dialog').close();
  toast('Draft saved.');
  if (state.folder === 'drafts') await loadMessages();
}));
$('#profile-button').addEventListener('click', () => { setSidebar(false); location.hash = 'account'; });

function setSidebar(open) {
  const mobile = window.matchMedia('(max-width:900px)').matches;
  const wasOpen = $('#sidebar').classList.contains('open');
  const visible = open && mobile;
  $('#sidebar').classList.toggle('open', visible);
  $('#sidebar').inert = mobile && !open;
  $('#main').inert = visible;
  $('#sidebar-backdrop').hidden = !visible;
  $('#mobile-menu').setAttribute('aria-expanded', String(visible));
  $('#mobile-menu').setAttribute('aria-controls', 'sidebar');
  document.body.classList.toggle('navigation-open', visible);
  if(visible && !wasOpen) {
    $('#sidebar-close').focus();
    $('.sidebar-navigation [aria-current=page]')?.scrollIntoView({block: 'nearest'});
  }
  else if(wasOpen && !visible && mobile) $('#mobile-menu').focus();
}
$('#sidebar-close').addEventListener('click',()=>setSidebar(false));
$('#sidebar').addEventListener('keydown',event=>{
  if(event.key!=='Tab'||!$('#sidebar').classList.contains('open'))return;
  const items=Array.from($('#sidebar').querySelectorAll('a[href],button:not([disabled])')).filter(el=>el.getClientRects().length);
  if(event.shiftKey&&document.activeElement===items[0]){event.preventDefault();items.at(-1).focus();}
  else if(!event.shiftKey&&document.activeElement===items.at(-1)){event.preventDefault();items[0].focus();}
});
$('#mobile-menu').addEventListener('click', () => setSidebar(!$('#sidebar').classList.contains('open')));
$('#sidebar-backdrop').addEventListener('click', () => setSidebar(false));
$('#sidebar').addEventListener('click', ev => {
  if (ev.target.closest('a')) setSidebar(false);
});
window.matchMedia('(max-width:900px)').addEventListener('change', () => setSidebar(false));
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape' && !document.querySelector('dialog[open]')) setSidebar(false);
});
setSidebar(false);
$('#refresh-mail').addEventListener('click', () => loadMessages());
$('#prev-page').addEventListener('click', () => {
  state.page--;
  loadMessages();
});
$('#next-page').addEventListener('click', () => {
  state.page++;
  loadMessages();
});
$('#mail-search').addEventListener('keydown', ev => {
  if (ev.key === 'Enter') {
    state.page = 1;
    loadMessages();
  }
});
$('#mail-search').addEventListener('search', () => {
  state.page = 1;
  loadMessages();
});
$('.skip-link').addEventListener('click', ev => {
  ev.preventDefault();
  $('#main').focus();
});
window.addEventListener('hashchange', () => route());
window.addEventListener('pageshow',event=>{if(event.persisted)boot();});
document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => document.getElementById(b.dataset.close).close()));
document.addEventListener('keydown', ev => {
  if (!state.user || /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName) || $('dialog[open]') || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === 'n') {
    ev.preventDefault();
    compose();
  }
  if (ev.key === '/' && !$('#mail-view').hidden) {
    ev.preventDefault();
    $('#mail-search').focus();
  }
});
// Quick navigation uses the same destinations and permission checks as the sidebar.
function navigationItems(){
  return [...folders.map(item=>[...item,'Mailbox']),...(state.user?.admin?adminPages.map(item=>[...item,'Administration']):[]),['account','Account','settings','Settings']];
}
function renderNavigation(){
  const term=$('#navigation-query').value.trim().toLocaleLowerCase();
  const matches=navigationItems().filter(item=>(item[1]+' '+item[3]).toLocaleLowerCase().includes(term));
  $('#navigation-results').replaceChildren(...matches.map(([id,label,sym,group])=>el('a',{href:'#'+id,'aria-current':id===(location.hash.slice(1)||'inbox')?'page':null,onclick:()=>{$('#navigation-dialog').close();setSidebar(false);if(location.hash==='#'+id)$('#main').focus();}},icon(sym),el('span',{},el('strong',{},label),el('small',{},group)),icon('chevron'))));
  $('#navigation-empty').hidden=matches.length>0;
}
function openNavigation(){
  if(!state.user)return;
  setSidebar(false);
  $('#navigation-query').value='';renderNavigation();
  if(!$('#navigation-dialog').open)$('#navigation-dialog').showModal();
  $('#navigation-query').focus();
}
$('#navigation-search').addEventListener('click',openNavigation);
$('#navigation-search-mobile').addEventListener('click',openNavigation);
$('#navigation-query').addEventListener('input',renderNavigation);
$('#navigation-dialog').addEventListener('keydown',event=>{
  const links=Array.from($('#navigation-results').querySelectorAll('a'));
  const current=links.indexOf(document.activeElement);
  if(event.key==='ArrowDown'||event.key==='ArrowUp'){
    event.preventDefault();
    const index=event.key==='ArrowDown'?(current+1)%links.length:(current<=0?links.length-1:current-1);
    links[index]?.focus();
  }else if(event.key==='Enter'&&event.target===$('#navigation-query')&&links.length){event.preventDefault();links[0].click();}
});
document.addEventListener('keydown',event=>{
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&state.user&&!document.querySelector('dialog[open]:not(#navigation-dialog)')){event.preventDefault();openNavigation();}
});
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
let themePreference = null;
try {
  themePreference = localStorage.getItem('kagnode-theme');
} catch {}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $('#theme-toggle').setAttribute('aria-label', theme === 'dark' ? 'Use light theme' : 'Use dark theme');
  $('#theme-toggle').setAttribute('title', theme === 'dark' ? 'Use light theme' : 'Use dark theme');
  $('meta[name="theme-color"]').setAttribute('content', theme === 'dark' ? '#000000' : '#f5f7fc');
}
applyTheme(themePreference === 'dark' || themePreference === 'light' ? themePreference : systemTheme.matches ? 'dark' : 'light');
systemTheme.addEventListener('change', e => {
  if (!themePreference) applyTheme(e.matches ? 'dark' : 'light');
});
$('#theme-toggle').addEventListener('click', () => {
  themePreference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme(themePreference);
  try {
    localStorage.setItem('kagnode-theme', themePreference);
  } catch {}
});
boot();
