"""Tieba 页面 DOM 到采集投影的脚本。"""

SEARCH_DOM = r"""(tab) => {
    const text = e => e?.innerText?.trim() || '';
    const thread = tab === '1';
    const selector = thread ? '.tbm-post-thread, .thread-new3' : '.tbm-floor-thread';
    return [...document.querySelectorAll(selector)].slice(0, 12).map(e => {
        const linkSelector = thread
            ? 'a[href*="/p/"]'
            : '.comment-link-zone[href*="/p/"], a[href*="/p/"]';
        const titleSelector = thread
            ? '.thread-title, .tbm-post-thread-title, .title-wrap'
            : '.tbm-floor-thread-post-thread';
        const link = e.querySelector(linkSelector);
        const title = text(e.querySelector(titleSelector));
        const reply = text(e.querySelector('.tbm-floor-thread-rich-text'));
        const forum = text(e.querySelector(
            '.forum-name, .forum-name-text, .tbm-floor-thread-forum'
        ));
        const abstract = text(e.querySelector('.abstract-wrap'));
        const body = reply || abstract || text(e);
        return {
            url: link?.href || '',
            title: (title || text(e.querySelector('a[href*="/p/"]'))).slice(0, 160),
            snippet: [forum, body].join(String.fromCharCode(10)).slice(0, 900),
            discovery_type: thread ? 'thread' : 'reply',
            matched_reply: reply || null
        };
    }).filter(item => item.url);
}"""

THREAD_DOM = r"""() => {
    const text = e => e?.innerText?.trim() || '';
    const forum = document.querySelector('.main-content .forum-name');
    const datePattern = new RegExp(
        String.raw`(?:20\d{2}[-/.年])?\d{1,2}[-/.月]\d{1,2}` +
        String.raw`(?:日)?(?:\s+\d{1,2}:\d{2})?`
    );
    const postedAt = element => {
        const metadata = element.querySelector(
            '.pc-pb-comments-desc .comment-desc-left, .lzl_time'
        );
        return text(metadata).match(datePattern)?.[0] || null;
    };
    const rows = [...document.querySelectorAll('.pb-comment-item')];
    const replies = [];
    for (const row of rows) {
        const parentId = row.getAttribute('data-id') || null;
        const floorText = text(row.querySelector('.comment-desc-left'));
        const floorMatch = floorText.match(/第\s*(\d+)\s*楼/);
        const direct = [...row.querySelectorAll('.pb-rich-text .pb-content-item')].filter(
            node => node.closest('.pb-comment-item') === row && !node.closest('.pb-lzl-item')
        );
        const content = direct.map(text).join('').slice(0, 600);
        if (content) replies.push({
            content, reply_id: parentId,
            floor: floorMatch ? Number(floorMatch[1]) : null,
            posted_at: postedAt(row),
            parent_reply_id: null, nested: false
        });
        for (const nested of row.querySelectorAll('.pb-lzl-item')) {
            const body = [...nested.querySelectorAll('.pb-content-item')]
                .map(text).join('').slice(0, 600);
            if (!body) continue;
            const id = nested.getAttribute('data-id') || nested.getAttribute('data-spid') || null;
            replies.push({
                content: body, reply_id: id, floor: floorMatch ? Number(floorMatch[1]) : null,
                posted_at: postedAt(nested),
                parent_reply_id: parentId, nested: true
            });
        }
    }
    const countText = [...document.querySelectorAll('button, a, span')]
        .map(text).find(value => /全部回复\s*[（(]\s*\d+\s*[)）]/.test(value)) || '';
    const count = countText.match(/全部回复\s*[（(]\s*(\d+)/);
    return {
        forum: text(forum).replace(/\s/g, ''),
        title: document.title.replace(/-百度贴吧$/, ''),
        original: text(document.querySelector('.center-content .pb-content-wrap')).slice(0, 2000),
        posted_at: text(document.querySelector('.center-content .post-num')) || null,
        loaded_reply_count: rows.length,
        total_replies: count ? Number(count[1]) : null,
        replies
    };
}"""
