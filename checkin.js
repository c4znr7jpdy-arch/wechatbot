const https = require('https');

// ================== 配置项 ==================
// 在这里添加所有需要签到的站点
const ACCOUNTS = [
    {
        name: 'GemAI',
        url: 'https://api.gemai.cc/api/user/checkin',
        userId: '183778',
        cookie: 'session=MTc3NTA0MTU1MnxEWDhFQVFMX2dBQUJFQUVRQUFEX2xQLUFBQVVHYzNSeWFXNW5EQWdBQm5OMFlYUjFjd05wYm5RRUFnQUNCbk4wY21sdVp3d0hBQVZuY205MWNBWnpkSEpwYm1jTUNRQUhaR1ZtWVhWc2RBWnpkSEpwYm1jTUJBQUNhV1FEYVc1MEJBVUFfUVdieEFaemRISnBibWNNQ2dBSWRYTmxjbTVoYldVR2MzUnlhVzVuREFvQUNFTXlNREF5TkRFNUJuTjBjbWx1Wnd3R0FBUnliMnhsQTJsdWRBUUNBQUk9fLziHUulo7b5Pia9R251PtRHs0SzdPLALri6r6YrzCPG'
    },
    {
        name: 'FreeAPI (DGBMC)',
        url: 'https://freeapi.dgbmc.top/api/user/checkin',
        userId: '20', 
        cookie: 'session=MTc3NDgzODg4N3xEWDhFQVFMX2dBQUJFQUVRQUFEX2tmLUFBQVVHYzNSeWFXNW5EQVlBQkhKdmJHVURhVzUwQkFJQUFnWnpkSEpwYm1jTUNBQUdjM1JoZEhWekEybHVkQVFDQUFJR2MzUnlhVzVuREFjQUJXZHliM1Z3Qm5OMGNtbHVad3dKQUFka1pXWmhkV3gwQm5OMGNtbHVad3dFQUFKcFpBTnBiblFFQWdBb0JuTjBjbWx1Wnd3S0FBaDFjMlZ5Ym1GdFpRWnpkSEpwYm1jTUNnQUlRVEl3TURJME1Uaz18-p6BiGKAZFnpFxqgQtONzFUO0qmW38Kr0BQrDpQW058='
    }
];
// ============================================

const commonHeaders = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'zh-CN',
    'cache-control': 'no-store',
    'content-length': '0',
    'origin': 'https://api.gemai.cc', // 可能需要根据不同站点调整，若接口校验不严可保持不变
    'priority': 'u=1, i',
    'sec-ch-ua': '"Chromium";v="146", "Not-A.Brand";v="24", "Microsoft Edge";v="146"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0'
};

/**
 * 执行单个账号的签到
 * @param {Object} account 
 */
function sendCheckin(account) {
    return new Promise((resolve) => {
        const urlObj = new URL(account.url);
        const headers = { 
            ...commonHeaders, 
            'cookie': account.cookie, 
            'new-api-user': account.userId,
            'origin': `${urlObj.protocol}//${urlObj.host}`,
            'referer': `${urlObj.protocol}//${urlObj.host}/console/personal`
        };

        const options = {
            method: 'POST',
            hostname: urlObj.hostname,
            path: urlObj.pathname + urlObj.search,
            headers: headers
        };

        console.log(`[${account.name}] 正在尝试签到...`);

        const req = https.request(options, (res) => {
            let data = '';
            res.on('data', (chunk) => data += chunk);
            res.on('end', () => {
                try {
                    const response = JSON.parse(data);
                    if (response.success === true) {
                        const date = response.data ? response.data.checkin_date : '未知日期';
                        console.log(`✅ [${account.name}] 成功！日期: ${date}`);
                    } else {
                        console.log(`❌ [${account.name}] 失败: ${response.message || '未知消息'}`);
                    }
                } catch (e) {
                    if (res.statusCode === 200) {
                        console.log(`✅ [${account.name}] HTTP 状态码 200, 可能已成功。`);
                    } else {
                        console.log(`❌ [${account.name}] 响应解析失败 (状态码: ${res.statusCode})`);
                    }
                }
                resolve();
            });
        });

        req.on('error', (e) => {
            console.error(`❌ [${account.name}] 网络错误: ${e.message}`);
            resolve();
        });

        req.end();
    });
}

/**
 * 批量签到
 */
async function startAll() {
    const today = new Date().toISOString().split('T')[0];
    console.log(`🔔 开始多站点批量签到任务 | 目标日期: ${today}\n`);
    
    for (const account of ACCOUNTS) {
        await sendCheckin(account);
    }
    
    console.log(`\n🎉 任务已全部完成！`);
}

startAll();
