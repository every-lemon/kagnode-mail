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
  demo: false,
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
  ['domains', 'Domains & DNS', 'globe']
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
  return name.replace(/<.*>/g, '').trim().split(/\s+/).slice(0, 2).map(x => x[0] || '').join('').toUpperCase() || 'M';
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
async function api(path, options = {}) {
  const headers = {
    'X-CSRF-Token': state.csrf,
    ...options.headers
  };
  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }
  const r = await fetch('/api' + path, {
    ...options,
    headers
  });
  const jsonResponse = (r.headers.get('Content-Type') || '').includes('application/json');
  const fallback = r.status === 429 ? 'Too many requests. Please wait a minute and try again.' :
    r.status === 413 ? 'The upload is too large. Keep attachments at 16 MiB or less.' :
    'Server unavailable. If sending mail, check delivery before retrying.';
  const data = jsonResponse ? await r.json() : {error: fallback};
  if (!r.ok || !jsonResponse) {
    if (r.status === 401 && path !== '/login' && path !== '/session') {
      await boot();
    }
    throw new Error(data.error || 'Request failed.');
  }
  return data;
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

function blank(title, description, sym = '✳') {
  return el('div', {
    class: 'empty-state'
  }, el('div', {
    class: 'empty-art'
  }, sym), el('h2', {}, title), el('p', {}, description));
}

function heading(title, subtitle, eyebrow = 'MAKE YOURSELF AT HOME') {
  $('#page-title').replaceChildren(title, el('span', {
    class: 'title-dot'
  }, '.'));
  $('#page-subtitle').textContent = subtitle;
  $('#page-eyebrow').textContent = eyebrow;
  $('#breadcrumb-current').textContent = title;
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
async function boot() {
  try {
    const s = await api('/session');
    Object.assign(state, s);
    $('#loading').hidden = true;
    $('#workspace').hidden = !s.user;
    $('#login-view').hidden = !!s.user;
    $('#demo-login').hidden = !s.demo;
    $('#preview-badge').hidden = !s.demo;
    if (s.user) {
      nav();
      await route();
    } else {
      state.messages = [];
      state.current = null;
      state.admin = null;
      state.generation++;
      for (const id of ['message-list', 'message-reader', 'admin-view', 'account-view']) document.getElementById(id).replaceChildren();
      $('#compose-form').reset();
      document.querySelectorAll('dialog[open]').forEach(d => d.close());
      document.title = 'Sign in · Morrow';
      $('#login-form [name=email]').focus();
    }
  } catch (e) {
    $('#loading').replaceChildren(blank('Could not open Morrow.', e.message), button('Try again', () => boot()));
  }
}
async function route() {
  if (!state.user) return;
  const page = location.hash.slice(1) || 'inbox';
  setSidebar(false);
  document.querySelectorAll('[data-page]').forEach(n => n.classList.toggle('active', n.dataset.page === page));
  $('#mail-view').hidden = !folders.some(f => f[0] === page);
  $('#admin-view').hidden = !adminPages.some(f => f[0] === page);
  $('#account-view').hidden = page !== 'account';
  document.title = (folders.find(f => f[0] === page)?.[1] || adminPages.find(f => f[0] === page)?.[1] || 'Account') + ' · Morrow';
  if (folders.some(f => f[0] === page)) {
    state.folder = page;
    state.page = 1;
    state.current = null;
    $('#mail-search').value = '';
    $('#mail-view').classList.remove('reading');
    heading(folders.find(f => f[0] === page)[1], {
      inbox: 'Good conversations start here.',
      sent: 'A little of you, out in the world.',
      drafts: 'Thoughts waiting for their moment.',
      archive: 'Out of the way. Always within reach.',
      junk: 'A quieter inbox starts here.',
      trash: 'Messages you’ve set aside.'
    } [page]);
    $('#message-reader').replaceChildren(blank('Room for a conversation.', 'Choose a message on the left, or start something new.'));
    await loadMessages();
  } else if (page === 'account') {
    renderAccount();
  } else if (state.user.admin && adminPages.some(f => f[0] === page)) {
    heading({
      overview: 'At a glance',
      users: 'People & access',
      domains: 'Domains & DNS'
    } [page], {
      overview: 'Your mail server, thoughtfully organized.',
      users: 'The right address. The right permissions.',
      domains: 'Give your mail a place to call home.'
    } [page], 'YOUR SERVER, YOUR RULES');
    $('#admin-view').replaceChildren(blank('One moment.', 'Loading your workspace…'));
    try {
      const data = await api('/admin');
      if (!state.user?.admin) return;
      state.admin = data;
      renderAdmin(page);
    } catch (e) {
      $('#admin-view').replaceChildren(blank('Could not load settings.', e.message));
    }
  } else {
    location.hash = 'inbox';
  }
}
async function loadMessages() {
  const gen = ++state.generation;
  $('#message-list').replaceChildren(blank('Just a moment.', 'Opening your mailbox…'));
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
    if (gen === state.generation) $('#message-list').replaceChildren(blank('Mailbox unavailable.', e.message, '↻'));
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
  $('#message-list').replaceChildren(...(nodes.length ? nodes : [blank('A little breathing room.', state.folder === 'drafts' ? 'Save an unfinished message from the composer.' : 'There are no messages here yet.')]));
}
async function openMessage(uid) {
  const folder = state.folder;
  state.current = {
    uid
  };
  renderList();
  $('#mail-view').classList.add('reading');
  $('#message-reader').replaceChildren(blank('Opening your message.', 'Just a moment…'));
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
  }, 'This permanently removes the message from Trash.')], 'Delete message', async () => mailAction('delete')) : mailAction('trash')), el('span', {
    class: 'toolbar-divider'
  }), ib('mail', 'Mark unread', () => mailAction('unread')), ib('star', item?.starred ? 'Remove star' : 'Star message', () => mailAction(item?.starred ? 'unstar' : 'star')), ib('shield', 'Move to junk', () => mailAction('junk')), el('span', {
    class: 'reader-meta-label'
  }, icon('mail'), 'A conversation, just for you'));
  const content = el('div', {
    class: 'reader-content'
  }, el('h2', {}, m.subject || '(No subject)'), el('div', {
    class: 'sender-block'
  }, el('span', {
    class: 'avatar'
  }, initials(senderName(m.sender))), el('div', {
    class: 'sender-details'
  }, el('strong', {title:m.sender}, senderName(m.sender)), el('small', {}, addressOf(m.sender)), el('small', {}, 'To ' + m.to + (m.cc ? ' · Cc ' + m.cc : ''))), el('span', {
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
    }, icon('shield'), 'Plain text view. Remote content stays blocked.', el('a', {
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
    toast('Message updated.');
    const uid = state.current.uid;
    await loadMessages();
    if (['read', 'unread', 'star', 'unstar'].includes(action)) {
      const m = state.messages.find(x => x.uid === uid);
      if (m && action.includes('star')) renderReader(state.current);
    } else {
      state.current = null;
      $('#message-reader').replaceChildren(blank('All taken care of.', 'Choose another conversation.'));
      $('#mail-view').classList.remove('reading');
    }
  } catch (e) {
    toast(e.message, true);
  }
}
let composeGeneration = 0;
async function compose(data = {}, attachments = []) {
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
      toast('Changes saved.');
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
    root.append(panel('A home for every conversation.', 'Manage the people and addresses that make this server yours.', button('Manage people', () => location.hash = 'users', 'button', 'users'), el('p', {
      class: 'panel-note'
    }, 'Administrators manage accounts and permissions, and have their own mailbox. Members can read and send their own mail using only the identities you grant them.'), el('div', {
      class: 'notice'
    }, 'Running on ' + a.hostname + '. DNS records and provider settings determine whether other servers can reach you. Review them before sending external mail.')));
    root.append(panel('Recent activity', 'Account and permission changes. Message content is never written to this log.', null, table(['WHEN', 'WHO', 'ACTION', 'TARGET'], a.audit.map(r => el('tr', {}, el('td', {}, r.created), el('td', {}, r.actor), el('td', {}, r.action), el('td', {}, r.target))))));
  } else if (page === 'users') {
    root.append(panel('Your people', a.users.length + ' accounts · individual mailboxes and permissions', button('Add person', createUser, 'button primary', 'plus'), table(['PERSON', 'ROLE', 'QUOTA', 'STATUS', ''], a.users.map(u => el('tr', {}, el('td', {}, el('strong', {}, u.name), el('small', {}, u.email)), el('td', {}, el('span', {
      class: 'badge' + (u.admin ? '' : ' neutral')
    }, u.admin ? 'Admin' : 'Member')), el('td', {}, u.quota_mb + ' MiB'), el('td', {}, el('span', {
      class: 'badge' + (u.active ? '' : ' orange')
    }, u.active ? 'Active' : 'Disabled')), el('td', {}, el('div', {
      class: 'inline-actions'
    }, button('Edit', () => editUser(u), 'text-button'), button('Send as', () => editIdentities(u), 'text-button'))))))));
    root.append(panel('Aliases', 'An extra address that delivers into an existing mailbox. Sending permission is assigned separately.', button('Add alias', createAlias, 'button', 'plus'), a.aliases.length ? table(['ADDRESS', 'DELIVERS TO', ''], a.aliases.map(r => el('tr', {}, el('td', {}, r.email), el('td', {}, r.destination), el('td', {}, button('Remove', () => removeAlias(r), 'text-button'))))) : el('p', {
      class: 'panel-note'
    }, 'Create an address such as hello@yourdomain.com, then choose who may send as it.')));
  } else {
    root.append(panel('Your domains', 'One server. A home for more than one domain.', button('Add domain', () => formDialog('Add a domain', [field('Domain name', 'name', 'text', '', {
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
    }, icon('globe'), el('div', {}, el('strong', {}, name), el('small', {}, 'Mail host · ' + a.hostname))), button('DNS setup', () => showDNS(name), 'button', 'settings')))));
    root.append(panel('Server address', a.hostname + ' · ' + a.ipv4, button('Edit public IP', () => formDialog('Public IPv4 address', [field('Public IPv4', 'ipv4', 'text', a.ipv4, {
      required: true
    }), el('p', {
      class: 'panel-note'
    }, 'Updates the DNS instructions only. Change the A and PTR records with your providers. This does not alter server networking or the mail hostname.')], 'Save address', async d => api('/admin/network', {
      method: 'PUT',
      body: {
        ipv4: d.get('ipv4')
      }
    })), 'button', 'settings')));
    root.append(panel('The last mile', 'A few settings live with your hosting and domain providers.', null, el('p', {
      class: 'panel-note'
    }, 'Point your mail hostname directly to your public IP address. Add the MX and TXT records shown in DNS setup, then set reverse DNS (PTR) with the provider that owns the IP. Ensure TCP 25 is open both ways; clients use 587 (STARTTLS) and 993 (TLS).'), el('div', {
      class: 'notice'
    }, 'If your DNS provider offers a web proxy, turn it off for the mail hostname. Do not publish an IPv6 address until outbound IPv6 and its reverse DNS are working.')));
  }
}

function createUser() {
  formDialog('Make room for someone new.', [field('Display name', 'name', 'text', '', {
    required: true,
    maxlength: 100
  }), field('Email address', 'email', 'email', '', {
    required: true,
    placeholder: 'sam@' + state.admin.domains[0]
  }), field('Temporary password · at least 12 characters', 'password', 'password', '', {
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
  }), 'Administrator — can manage people, permissions, and domains')], 'Create account', async (d) => api('/admin/users', {
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
  }), field('New password · leave blank to keep current', 'password', 'password', '', {
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
  }), 'Account is active'), el('p', {
    class: 'panel-note'
  }, 'Saving signs this person out of webmail. Mail is preserved when an account is disabled.')], 'Save changes', async d => {
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
  }, u.name + ' can always send as ' + u.email + '. Choose any additional addresses. These permissions apply in webmail and native SMTP clients.'), el('div', {
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
  }, 'Create another mailbox or alias to add a sending identity.')]))], 'Save permissions', async d => {
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
  formDialog('An address with a purpose.', [field('Alias email address', 'email', 'email', '', {
    required: true,
    placeholder: 'hello@' + state.admin.domains[0]
  }), el('label', {}, 'Deliver incoming mail to', el('select', {
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
  }, 'Incoming mail to this alias will stop, and its send-as permissions will be removed. Existing messages stay in the destination mailbox.')], 'Remove alias', async () => api('/admin/aliases/' + encodeURIComponent(r.email), {
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
    }, 'Add these records at your DNS provider. Use the full host name below, or its relative label if your provider appends the domain automatically.'), button(check ? 'Check again' : 'Check published DNS', ev => busy(ev.currentTarget, () => showDNS(name, true)), 'button', 'refresh'));
    body.append(el('div', {
      class: 'notice'
    }, 'DNS changes can take time to appear. A match here checks records only; it does not prove port reachability or inbox delivery.'));
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
          toast('Record copied.');
        } catch {
          toast('Clipboard unavailable. Select and copy the value below.', true);
        }
      })), el('code', {}, r.value || 'Set the server’s public IP during installation.'), el('p', {}, r.note), r.observed?.length ? el('p', {}, 'Observed: ' + r.observed.join(' · ')) : null));
    }
    dialog('DNS setup · ' + name, body);
  } catch (e) {
    toast(e.message, true);
  }
}

function renderAccount() {
  heading('Your account', 'A few details that make this space yours.', 'FEEL RIGHT AT HOME');
  const u = state.user;
  const f = el('form', {
    class: 'stack-form'
  }, field('Current password', 'current', 'password', '', {
    required: true,
    autocomplete: 'current-password'
  }), field('New password · at least 12 characters', 'password', 'password', '', {
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
  }, panel('A little about you', null, null, el('p', {}, u.name), el('p', {
    class: 'panel-note'
  }, u.email), el('span', {
    class: 'badge'
  }, u.admin ? 'Administrator' : 'Member'), el('p', {
    class: 'panel-note'
  }, 'Mailbox quota: ' + u.quota_mb + ' MiB'), el('h3', {}, 'Your sending identities'), ...state.identities.map(v => el('p', {
    class: 'panel-note'
  }, v))), panel('Fresh password, fresh start.', 'Changing your password signs out all web sessions.', null, f)), panel('Bring your favorite mail app.', 'Use your full email address and password to sign in.', null, table(['SERVICE', 'SERVER', 'PORT', 'SECURITY'], [el('tr', {}, el('td', {}, 'Incoming · IMAP'), el('td', {}, state.hostname), el('td', {}, '993'), el('td', {}, 'SSL/TLS')), el('tr', {}, el('td', {}, 'Outgoing · SMTP'), el('td', {}, state.hostname), el('td', {}, '587'), el('td', {}, 'STARTTLS · authentication required'))])));
}
$('#login-form').addEventListener('submit', ev => {
  ev.preventDefault();
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
$('#profile-button').addEventListener('click', () => location.hash = 'account');

function setSidebar(open) {
  const mobile = window.matchMedia('(max-width:730px)').matches;
  $('#sidebar').classList.toggle('open', open && mobile);
  $('#sidebar').inert = mobile && !open;
  $('#sidebar-backdrop').hidden = !mobile || !open;
  $('#mobile-menu').setAttribute('aria-expanded', String(open && mobile));
}
$('#mobile-menu').addEventListener('click', () => setSidebar(!$('#sidebar').classList.contains('open')));
$('#sidebar-backdrop').addEventListener('click', () => setSidebar(false));
$('#sidebar').addEventListener('click', ev => {
  if (ev.target.closest('a')) setSidebar(false);
});
window.matchMedia('(max-width:730px)').addEventListener('change', () => setSidebar(false));
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') setSidebar(false);
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
window.addEventListener('hashchange', () => route());
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
try {
  document.documentElement.dataset.theme = localStorage.getItem('morrow-theme') || 'light';
} catch {}
$('#theme-toggle').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem('morrow-theme', theme);
  } catch {}
});
const now = new Date();
$('#heading-date').replaceChildren(el('strong', {}, now.toLocaleDateString(undefined, {
  weekday: 'long'
})), now.toLocaleDateString(undefined, {
  month: 'long',
  day: 'numeric',
  year: 'numeric'
}));
boot();
