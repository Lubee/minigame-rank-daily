import gplay from 'google-play-scraper';

try {
  const options = {
    country: 'us',
    lang: 'en',
    requestOptions: { timeout: process.argv[2] === '--app' ? Number(process.argv[4] ?? 25000) : 15000, retry: { limit: 0 } },
  };
  let result;
  if (process.argv[2] === '--app') {
    const appId = process.argv[3];
    if (!/^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$/.test(appId ?? '')) {
      throw new Error('Invalid Google Play package');
    }
    result = await gplay.app({ ...options, appId });
  } else {
    const count = Number(process.argv[2] ?? 100);
    if (!Number.isInteger(count) || count < 1 || count > 100) {
      throw new Error('Expected a chart size between 1 and 100');
    }
    const chart = process.argv[3] ?? 'free';
    const cluster = { free: 'topselling_free', new_free: 'topselling_new_free', new_paid: 'topselling_new_paid' }[chart];
    if (!cluster) throw new Error('Invalid Google Play chart');
    // The pinned library validates only its public constants; adapt the official RPC without patching it.
    options.requestOptions.hooks = { beforeRequest: [request => {
      const body = String(request.body);
      if (!body.includes('topselling_free')) throw new Error('Google Play RPC payload changed');
      request.body = body.replace('topselling_free', cluster);
      request.headers['content-length'] = String(Buffer.byteLength(request.body));
    }] };
    result = await gplay.list({
      ...options, category: gplay.category.GAME,
      collection: gplay.collection.TOP_FREE, num: count, fullDetail: false,
    });
  }
  process.stdout.write(JSON.stringify(result));
} catch (error) {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
}
