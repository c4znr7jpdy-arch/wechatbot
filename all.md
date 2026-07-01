#### 发送图片
##### 请求

 ``` 
 {
  "data": {
    "aes_key": "9a2c7526345f3d1b18bc51c457b7bd2a",
    "file_id": "3057020100044b3049020100020462ac90a202032f597d0204ed9f3cb7020462df8e01042437323164383633332d373266302d346130362d393539372d3739363262373464616665350204011800020201000405004c4f2900",
    "file_md5": "b406bbad9b1b097a379d0a0bd8e53412",
    "file_size": 2516803,
    "thumb_file_size": 32693,
    "crc32": 719191615,
    "to_wxid": "filehelper"
  },
  "type": 11231
}

 ```
> CDN信息通过CDN上传获取

##### 返回示例 

``` 
 {
  "data": {
    "aesKey": "9a2c7526345f3d1b18bc51c457b7bd2a",
    "baseResponse": {
      "ret": 0
    },
    "clientImgId": {
      "string": "1d41de6ee23e8xxxxx3f2c325"
    },
    "createTime": 0,
    "dataLen": 0,
    "fileId": "3057020100044b3049020100020462ac90a202032f597d0204ed9f3cb7020462df8e01042437323164383633332d373266302d346130362d393539372d3739363262373464616665350204011800020201000405004c4f2900",
    "fromUserName": {
      "string": "wxid_d7zqkxxxxx2"
    },
    "msgId": 774251484,
    "newMsgId": "657578450xxxx5688",
    "startPos": 2516803,
    "toUserName": {
      "string": "filehelper"
    },
    "totalLen": 2516803
  },
  "type": 11231
}

```
# 首页
**接口调用方式为POST提交**





##### 例子请求

 ``` 
{
    "type": 10,
    "data": {},
    "trace": 1
}
 

 ```
type 为指令类型值，必须是整数
data 为指令附加内容
trace 由调用方传递过来，接口会原样返回，可用于指令同步。（ps: 所有接口都有）


**DLL接口**

DLL有两个`Loader.dll`和`Helper.dll`

**DLL文件说明：**

| 文件名 | 说明 |
| --- | --- |
| Loader.dll | 管理端，用于多开微信和与微信交互 |
| Helper.dll | 客户端，接收指令并发送数据给管理端 |

`WeChatHelper.dll可以以微信版本号做为名称，先使用GetUserWeChatVersion函数获取用户电脑上的微信版本，判断是否支持，如果支持，使用InjectWeChat注入dll`

**Loader.dll的导出函数:**

1.  InitWeChatSocket  
    `用于socket的回调处理`  
    函数原型：  
    `BOOL __stdcall InitWeChatSocket(IN DWORD dwConnectCallback, IN DWORD dwRecvCallback, IN DWORD dwCloseCallback)`  
    其中dwConnectCallback是一个函数指针类型, 在有新客户端加入时调用，结构如下：
    
         `void __stdcall MyConnectCallback(int iClientId)` 传入的一个参数是socket的客户ID,返回值为空 
        
    dwRecvCallback是一个函数指针类型,在接收到新消息时调用，结构如下：
    
         `void __stdcall MyRecvCallback(int iClientId, char* szJsonData, int iLen)` 
        
    dwCloseCallback是一个函数指针类型，在客户端退出时调用，结果如下:
    
         `void __stdcall MyCloseCallback(int iClientId)`
        
    
2.  GetUserWeChatVersion  
    `获取当前用户的电脑上安装的微信版本，如： 2.6.7.57`  
    函数原型：  
    `BOOL __stdcall GetUserWeChatVersion(OUT LPSTR szVersion);`  
    传一个ANSI字符串缓冲区的指针，长度30即可， 这个函数可以先获取当前用户电脑上安装的微信版本，然后判断我们的dll是否支持，如果不支持就提示用户下载我们支持的版本。
    
3.  InjectWeChat  
    `用于智能多开，并注入dll, 注入成功返回微信的进程ID, 失败返回0`  
    函数原型：  
    `DWORD __stdcall InjectWeChat(IN LPCSTR szDllPath);`  
    如果需要一个软件，管理多个微信，多次调用这个函数实现，通过socket回调管理客户端
    
4.  SendWeChatData  
    用于向微信发送指令，指令内容参考功能类，·  
    函数原型:  
    `BOOL __stdcall SendWeChatData(IN CONNID dwClientId, IN LPCSTR szJsonData);`
    
5.  DestroyWeChat  
    `主程序退出前，执行释放函数，用于卸载DLL和关闭socket服务端`  
    函数原型:  
    `BOOL __stdcall DestroyWeChat();`
    
6.  UseUtf8  
    `在所有接口前执行，执行后接口全部使用utf8编码传输`  
    函数原型:  
    `BOOL __stdcall UseUtf8();`
    
7.  InjectWeChat2  
    `用于智能多开,跟InjectWeChat功能相同，多了一个参数传递指定微信的安装路径，并注入dll, 注入成功返回微信的进程ID, 失败返回0`  
    函数原型：  
    `DWORD __stdcall InjectWeChat2(IN LPCSTR szDllPath, IN LPCSTR szWeChatExePath);`  
    如果需要一个软件，管理多个微信，多次调用这个函数实现，通过socket回调管理客户端
    
8.  InjectWeChatPid  
    `注入指定的微信进程，参数1： 微信进程id, 参数2： dll路径`  
    函数原型：  
    `DWORD __stdcall InjectWeChatPid(IN DWORD dwPid, IN LPCSTR szDllPath)`
    

1.  InjectWeChatMultiOpen  
    `多开一个新的微信进程并注入，不维护已经打开的微信进程，需要两个参数，参数1：WeChatHelper.dll的路径，参数2：指定要启动微信（WeChat.exe）的完整路径，如果不提供，可以设置0或空字符串，将自动读取微信的安装目录`  
    函数原型：  
    `DWORD __stdcall InjectWeChatMultiOpen(IN LPCSTR szDllPath, IN LPCSTR szWeChatExePath);`·

# 登录信息

##### 请求

 ``` 
 {
  "type": 11028,
  "data": {}
}
 

 ```


##### 返回示例 

``` 
 {
  "data": {
    "account": "yi-xxx8xx",
    "avatar": "http://wx.qlogo.cn/mmhead/ver_1/sSiaregP9Fxxxx/0",
    "nickname": "昵称",
    "wxid": "wxid_d7zxxxxx"
  },
  "type": 11028
}

```






# 获取好友列表

##### 请求

 ``` 
 {
  "type": 11030,
  "data": {}
}
 

 ```


##### 返回示例 

``` 
 {
  "data": [
    {
      "account": "wx173xxxx",
      "avatar": "http://wx.qlogo.cn/mmhead/ver_1/T0oZxxxx/0",
      "city": "Chaoyang",
      "country": "CN",
      "nickname": "在路上",
      "province": "Beijing",
      "remark": "",
      "sex": 1,   // 性别 1男 2女 0保密
      "wxid": "wxid_jxxxxx"  // wxid
    }
  ],
  "type": 11030
}

```






# 获取好友信息

##### 请求

 ``` 
 {
  "type": 11029,
  "data": {
    "wxid": "wxid_j0bxxxx2"
  }
}
 

 ```


##### 返回示例 

``` 
 {
  "data": {
    "account": "wxxxxxx60",
    "avatar": "http://wx.qlogo.cn/mmhead/ver_1/xxxxxxxx/0",
    "city": "Chaoyang",
    "country": "CN",
    "nickname": "在路上",
    "province": "Beijing",
    "remark": "",
    "sex": 1,
    "wxid": "wxid_j0bxxxx2"
  },
  "type": 11029
}

```






# 获取群聊列表

##### 请求

 ``` 
 {
  "type": 11031,
  "data": {
    "detail": 1
  }
}


 ```
detail=1时 返回memeber_list,0时不返回

##### 返回示例 

``` 
 {
  "data": [
    {
      "avatar": "http://wx.qlogo.cn/mmcrhead/icXjjxxxxxx/0",
      "is_manager": 0,
      "manager_wxid": "wxid_dhgxvxxxxxxx",
      "nickname": "心花怒FUN",
      "total_member": 350,
      "wxid": "2547xxxxx@chatroom",
      "member_list": ["wxid_lllxxxxxx"]
    }
  ],
  "type": 11031
}

```






# 获取群聊信息

##### 请求

 ``` 
 {
  "data": {
    "room_wxid": "254xxxx5@chatroom"
  },
  "type": 11032
}


 ```


##### 返回示例 

``` 
 {
  "data": {
    "extend": "",
    "group_wxid": "210644xxx@chatroom",
    "member_list": [
      {
        "account": "yi-1xxxxx8",
        "avatar": "http://wx.qlogo.cn/mmhead/ver_1/sxxxxxxxxx/0",
        "city": "Wuhan",
        "country": "CN",
        "display_name": "",  //群内昵称
        "nickname": "你看起来很好吃",
        "province": "Hubei",
        "remark": "",
        "sex": 1,
        "wxid": "wxid_dxxxxxxxxx2"
      }
    ],
    "total": 3
  },
  "type": 11032
}

```






# 显示群成员昵称

##### 请求

 ``` 
 {
  "data": {
    "room_wxid": "xxxxxxxx@chatroom",
    "status": 0  
  },
  "type": 11075
}


 ```
status=0关闭，1开启

##### 返回示例 

``` 
 {
  "data": {
    "oplogRet": {
      "count": 1,
      "ret": [
        {
          "ret": 0
        }
      ]
    },
    "ret": 0
  },
  "type": 11075
}

```






# CDN初始化

##### 请求

 ``` 
 {
  "type": 11228,
  "data": {}
}


 ```
用于初始化CDN环境，收到登录消息后执行一次即可

##### 返回示例 

``` 
 {
  "data": {
    "status": 1
  },
  "type": 11228
}

```
status=1 初始化成功





# CDN上传

##### 请求

 ``` 
 {
  "type": 11229,
  "data": {
    "file_type": 2,  // 如下
    "file_path": "文件路径"
  }
}


 ```
 file_type值如下：
 
|  值 | 说明  |
| ------------ | ------------ |
|  1 |原图   |
|   2|  中图（上传图片用这个） |
|   3|   缩略图|
|   4|  视频 |
|   5|  文件&GIF |
##### 返回示例 

``` 
 {
  "data": {
    "aes_key": "24b6123e04d1d08702143658917c85e7",
    "crc32": 2264267254,
    "error_code": 0,
    "file_id": "3057020100044b3049020100020462ac90a202032df4d9020428fb131b020462e15ab0042435306239613864642d626562392d343535612d383663662d6431666335373935396266630204011400050201000405004c4c6d00",
    "file_key": "35a6605a-972f-468a-97ff-508fc5555163",
    "file_md5": "a2ed2d6159ad865c93ba25db74df4616",
    "file_path": "文件路径",
    "file_size": 393652,
    "thumb_file_md5": "",
    "thumb_file_size": 0
  },
  "type": 11229
}

```







# CDN下载

##### 请求

 ``` 
 {
  "type": 11230,
  "data": {
    "file_id": "3057020100044b304902010002045b9507af02032f59e1020437c7587d020462e0fa8a042432343235626665622d303635652d346439662d626562632d3436656362613162316635390204011800010201000405004c4dfd00",
    "file_type": 2,
    "aes_key": "8819302807f6d22258cc77a562008c5e",
    "save_path": "d:\\test.jpg"  // 保存路径
  }
}


 ```
> 注意 下载图片，先使用file_type=2下载，若失败再用file_type=1下载

| 值  |说明   |
| ------------ | ------------ |
| 1  |  原图 |
|  2 |  中图（上传图片用这个） |
|  3 |  缩略图 |
| 4  |  视频 |
|  5 |  文件&GIF |

##### 返回示例 

``` 
 {
  "data": {
    "error_code": 0, 
    "file_id": "3057020100044b304902010002045b9507af02032f59e1020437c7587d020462e0fa8a042432343235626665622d303635652d346439662d626562632d3436656362613162316635390204011800010201000405004c4dfd00",
    "file_size": 1383023,
    "save_path": "d:\\test.jpg"
  },
  "type": 11230
}

```






# 发送消息（CDN）

##### 请求

 ``` 
 {
  "type": 11237,
  "data": {
    "to_wxid": "filehelper",
    "content": "new msg interface"
  }
}


 ```
> to_wxid 接收者的微信id
content 文本消息内容

##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "ret": 0
    },
    "count": 1,
    "msgResponseList": [
      {
        "clientMsgId": 1658910443,
        "createTime": 1658910443,
        "msgId": 0,
        "newMsgId": "267817xxxxxxxxxx2",
        "ret": 0,
        "serverTime": 1658910445,
        "toUserName": {
          "string": "filehelper"
        },
        "type": 1
      }
    ]
  },
  "type": 11237
}

```






# 发送群@消息（CDN）

##### 请求
**@所有人**
 ``` 
 {
  "type": 11240,
  "data": {
    "to_wxid": "224114xxxx@chatroom",
    "content": "好啊",
    "at_all": 1
  }
}


 ```
**@指定群成员**
 ``` 
 {
  "type": 11240,
  "data": {
    "to_wxid": "22411xxxxxx@chatroom",
    "content": "好啊 {$@}",
    "at_list": [
      "wxid_qef252xxxxxx"
    ]
  }
}
 ```
** 发送内容中{$@}占位符说明：**

文本消息的content的内容中设置占位字符串 `{$@}`,这些字符的位置就是最终的@符号所在的位置
假设这两个被@的微信号的群昵称分别为`aa,bb`
则实际发送的内容为 `"test,你好@ aa,你好@ bb.早上好"(占位符被替换了)`

> 占位字符串的数量必须和at_list中的微信数量相等.
如果不写`{$@}`，需要自己构造@昵称

##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "ret": 0
    },
    "count": 1,
    "msgResponseList": [
      {
        "clientMsgId": 1658910443,
        "createTime": 1658910443,
        "msgId": 0,
        "newMsgId": "267817xxxxxxxxxx2",
        "ret": 0,
        "serverTime": 1658910445,
        "toUserName": {
          "string": "22411xxxxxx@chatroom"
        },
        "type": 1
      }
    ]
  },
  "type": 11240
}

```






# 发送图片（CDN）

##### 请求

 ``` 
 {
  "data": {
    "aes_key": "9a2c7526345f3d1b18bc51c457b7bd2a",
    "file_id": "3057020100044b3049020100020462ac90a202032f597d0204ed9f3cb7020462df8e01042437323164383633332d373266302d346130362d393539372d3739363262373464616665350204011800020201000405004c4f2900",
    "file_md5": "b406bbad9b1b097a379d0a0bd8e53412",
    "file_size": 2516803,
    "thumb_file_size": 32693,
    "crc32": 719191615,
    "to_wxid": "filehelper"
  },
  "type": 11231
}

 ```
> CDN信息通过CDN上传获取

##### 返回示例 

``` 
 {
  "data": {
    "aesKey": "9a2c7526345f3d1b18bc51c457b7bd2a",
    "baseResponse": {
      "ret": 0
    },
    "clientImgId": {
      "string": "1d41de6ee23e8xxxxx3f2c325"
    },
    "createTime": 0,
    "dataLen": 0,
    "fileId": "3057020100044b3049020100020462ac90a202032f597d0204ed9f3cb7020462df8e01042437323164383633332d373266302d346130362d393539372d3739363262373464616665350204011800020201000405004c4f2900",
    "fromUserName": {
      "string": "wxid_d7zqkxxxxx2"
    },
    "msgId": 774251484,
    "newMsgId": "657578450xxxx5688",
    "startPos": 2516803,
    "toUserName": {
      "string": "filehelper"
    },
    "totalLen": 2516803
  },
  "type": 11231
}

```






# 发送视频（CDN）


##### 请求

 ``` 
 {
  "data": {
    "aes_key": "6f18b60119a77bbdxxxxxxx6205",
    "file_id": "3057020100044b3049020100020462ac90a202032df4d9020423fb131b020462dfd5e2042463313732333737662d653765382d346331302d623xxxxd3063326139623263663361660204011400040201000405004c4dfd00",
    "file_md5": "a0ed8685a289717023xx4d2809d",
    "file_size": 1383023,
    "thumb_file_size": 16798,
    "to_wxid": "filehelper"
  },
  "type": 11233
}
 

 ```
> CDN信息通过CDN上传获取

##### 返回示例 

``` 
 {
  "data": {
    "actionFlag": 0,
    "aeskey": "6f18b60119a77bbd358xxx4f436205",
    "baseResponse": {
      "ret": 0
    },
    "clientMsgId": "1e516765409758xx5549a2eec49",
    "msgId": 774252078,
    "msgSource": "<msgsource>\n\t<sec_msg_node>\n\t\t<uuid>e06bxx21b1aba670f25_</uuid>\n\t</sec_msg_node>\n</msgsource>\n",
    "newMsgId": "1291076585093205173",
    "rawAeskey": "",
    "rawVideoNeedReupload": false,
    "thumbStartPos": 16798,
    "videoNeedReupload": false,
    "videoStartPos": 1383023
  },
  "type": 11233
}

```





# 发送文件（CDN）

##### 请求

 ``` 
 {
  "data": {
    "aes_key": "129604c1601387b33c9c165fccf774eb",
    "file_id": "3057020100044b3049020100020462ac90a202032df4d90204d4fb131b020462dff3d3042439336463313435332d306137312d343165322d616431632d3632366237326665323035610204011400050201000405004c52ad00",
    "file_md5": "af5b4cebe26bc5265e6085f15623e46c",
    "file_name": "block_1.bin",
    "file_size": 2097152,
    "to_wxid": "filehelper"
  },
  "type": 11235
}
 

 ```
> CDN信息通过CDN上传获取

##### 返回示例 

``` 
 {
  "data": {
    "actionFlag": 0,
    "aeskey": "129604c1601387b33c9c165fccf774eb",
    "appId": "wx6618f1cfc6c132f8",
    "baseResponse": {
      "ret": 0
    },
    "clientMsgId": "efc96c99c4a3xx77aa80668c73ea",
    "createTime": 1658911557,
    "fromUserName": "wxid_d7zxxxx89r22",
    "msgId": 774251486,
    "msgSource": "<msgsource>\n\t<sec_msg_node>\n\t\t<uuid>3b926274b35858xxxxc815891c8250_</uuid>\n\t</sec_msg_node>\n</msgsource>\n",
    "newMsgId": "1892xxxx818",
    "toUserName": "filehelper",
    "type": 6
  },
  "type": 11235
}

```






# 发送链接卡片（CDN）

##### 请求

 ``` 
 {
  "data": {
    "to_wxid": "filehelper",
    "title": "百度一下",
    "desc": "用科技让复杂的世界更简单！",
    "url": "https://www.baidu.com",
    "image_url": "https://img.jbzj.com/file_images/Illustrator/201702/2017020411591786.png"
  },
  "type": 11236
}


 ```


##### 返回示例 

``` 
 {
  "data": {
    "actionFlag": 0,
    "aeskey": "",
    "appId": "",
    "baseResponse": {
      "errMsg": {},
      "ret": 0
    },
    "clientMsgId": "e15b1c28e04xxxxxx126fe020420",
    "createTime": 1659367516,
    "fromUserName": "wxid_d7zqk6xxxxr22",
    "msgId": 774255548,
    "msgSource": "<msgsource>\n\t<sec_msg_node>\n\t\t<uuid>6cc945xxx86ce892ba06c64_</uuid>\n\t</sec_msg_node>\n</msgsource>\n",
    "newMsgId": "125665573xxx399",
    "toUserName": "filehelper",
    "type": 5
  },
  "type": 11236
}

```






# 发送gif图（CDN）

##### 请求

 ``` 
 {
  "data": {
    "aes_key": "24b6123e04d1d08702143658917c85e7",
    "file_id": "3057020100044b3049020100020462ac90a202032df4d9020428fb131b020462e15ab0042435306239613864642d626562392d343535612d383663662d6431666335373935396266630204011400050201000405004c4c6d00",
    "file_md5": "a2ed2d6159ad865c93ba25db74df4616",
    "file_size": 393652,
    "to_wxid": "filehelper"
  },
  "type": 11241
}
 

 ```
> CDN信息使用CDN上传接口获取，file_type=5

##### 返回示例 

``` 
 {
  "data": {
    "actionFlag": 0,
    "aeskey": "",
    "appId": "wx6618f1cfc6c132f8",
    "baseResponse": {
      "ret": 0
    },
    "clientMsgId": "40e5f72b39026df05xxxxx25ade4e3",
    "createTime": 1658937218,
    "fromUserName": "wxid_d7zqkxxxxxx22",
    "msgId": 774251956,
    "msgSource": "<msgsource>\n\t<sec_msg_node>\n\t\t<uuid>6e8fccd23ef164e9b459ca582331bb65_</uuid>\n\t</sec_msg_node>\n</msgsource>\n",
    "newMsgId": "569589847xxx525",
    "toUserName": "filehelper",
    "type": 8
  },
  "type": 11241
}

```






# 发送小程序（CDN）

##### 请求

 ``` 
 {
  "type": 11242,
  "data": {
    "to_wxid": "filehelper",

    "username": "gh_870576f3c6f9@app",
    "appid": "wxde8ac0a21135c07d",
    "appname": "美团团购丨优选外卖单车美食酒店",
    "appicon": "http://wx.qlogo.cn/mmhead/Q3auHgzwzM5IfaiappYJdWCApgZnQUtjqDLBOB2U2l4nsfASxgxkubQ/96",
    "title": "吃喝玩乐 尽在美团",
    "page_path": "index/pages/mt/mt.html",

    // cdn参数为小程序封面，可以从cdn上传图片处获得
    "file_id": "3057020100044b30490201000204bbc864f302032df4d9020468fb131b020462e164d9042432323934313039632d626235372d346662352d393965652d3039386636356438616130650204011400030201000405004c4c6d00",
    "aes_key": "3204266d4e5aec642fea50cc8b40524a",
    "file_md5": "02794251894b47af3a946e19d47757cc",
    "file_size": 35967
  }
}
 ```


##### 返回示例 

``` 
 {
  "data": {
    "actionFlag": 0,
    "aeskey": "",
    "appId": "",
    "baseResponse": {
      "ret": 0
    },
    "clientMsgId": "55df57e4ecdb390fxxxe05c1fe038a3",
    "createTime": 1658941107,
    "fromUserName": "wxid_d7zqk6yxxxxxx2",
    "msgId": 774252002,
    "msgSource": "<msgsource>\n\t<sec_msg_node>\n\t\t<uuid>6266223d7fd4bfxxxxabeafc_</uuid>\n\t</sec_msg_node>\n</msgsource>\n",
    "newMsgId": "60306xxxxx968834",
    "toUserName": "filehelper",
    "type": 33
  },
  "type": 11242
}

```






# 发送名片（CDN）

##### 请求

 ``` 
 {
  "data": {
    "to_wxid": "filehelper",
    "username": "wxid_l4xcdxxx21",
    "nickname": "Andy",
    "avatar": "http://wx.qlogo.cn/mmhead/ver_1/fvtXI5DVao03WPwDMhjIcFzR70Svz1JFe3u3icxf6WhtpeGGI7KIfUTKvK4d6ib1kDFVbI6SCkFf9078KpNXBibjibxxxx7otTg/132"
  },
  "type": 11239
}
 

 ```


##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "ret": 0
    },
    "count": 1,
    "msgResponseList": [
      {
        "clientMsgId": 1658986935,
        "createTime": 1658986935,
        "msgId": 0,
        "newMsgId": "207226xxxxxxx120",
        "ret": 0,
        "serverTime": 1658986936,
        "toUserName": {
          "string": "filehelper"
        },
        "type": 42
      }
    ]
  },
  "type": 11239
}

```






# 发送gif图new（CDN）

##### 请求
1.1使用文件发送
 ``` 
 {
  "type": 11254,
  "data": {
    "path": "d:\\test.gif",
    "to_wxid": "filehelper"
  }
}


 ```
1.2使用md5参数发送
 ``` 
 {
  "type": 11254,
  "data": {
    "md5": "5190505E9E656BF0E72CBBF2DAA01C4F",
    "size": 410883,
    "to_wxid": "filehelper"
  }
}
 ```

##### 返回示例 

``` 
 {
  "data": {
    "actionFlag": 0,
    "baseResponse": {
      "errMsg": {},
      "ret": 0
    },
    "emojiItem": [
      {
        "md5": "5190505E9E656BF0E72CBBF2DAA01C4F", // 文件md5
        "msgId": 774295222,
        "newMsgId": "6982243159xxxxxx2897416",  // 消息id
        "ret": 0,  // 用于判断是否发送成功 0 成功
        "startPos": 410883,
        "totalLen": 410883         // 文件长度
      }
    ],
    "emojiItemCount": 1
  },
  "type": 11254
}

```






# 发消息（普通）

##### 请求

 ``` 
 {
  "data": {
    "to_wxid": "wxid_xxxxxxx",
    "content": "你好，世界"
  },
  "type": 11036
}
 

 ```









# 发送群@消息

##### 请求
**@所有人**
 ``` 
  {
    "data": {
        "to_wxid": "xxxxxx@chatroom",
        "content": "@所有人 早上好",
        "at_list": ["notify@all"]
    },
    "type": 11037
  }
 ```
** @指定群成员**
  ``` 
  {
    "data": {
        "to_wxid": "xxxxxx@chatroom",
        "content": "test,你好{$@},你好{$@}.早上好",
        "at_list": ["wxid_xxxxxx","wxid_xxxxxxx"]
    },
    "type": 11037
  }
 ```
 **发送内容中{$@}占位符说明：**

文本消息的content的内容中设置占位字符串` {$@}`,这些字符的位置就是最终的@符号所在的位置
假设这两个被@的微信号的群昵称分别为`aa,bb`
则实际发送的内容为 `"test,你好@ aa,你好@ bb.早上好"(占位符被替换了)`


> 占位字符串的数量必须和at_list中的微信数量相等.
如果不传占位符， 需要在content自己组合@昵称







# 发送名片

##### 请求

 ``` 
  {
    "data": {
        "to_wxid": "wxid_xxxxxx",
        "card_wxid": "wxid_xxxxxx"
    },
    "type": 11038
  }


 ```


# 发送连接消息



##### 请求

 ``` 
  {
    "data": {
        "to_wxid": "wxid_xxxxxxx",
        "title" : "百度一下",
        "desc": "用科技让复杂的世界更简单！",
        "url" : "https://www.baidu.com",
        "image_url" : "http://www.xxx.com/xxx.jpg"    
    }
    "type": 11039
  }


 ```


# 发送图片消息

##### 请求

 ``` 
  {
    "data": {
        "to_wxid": "wxid_xxxxxx",
        "file": "C:\\a.jpg"
    },
    "type": 11040
  }


 ```









# 发送文件

##### 请求

 ``` 
 {
  "data": {
    "to_wxid": "wxid_xxxxxx",
    "file": "C:\\a.pdf"
  },
  "type": 11041
}
 

 ```







# 发送视频消息

##### 请求

 ``` 
 {
  "data": {
    "to_wxid": "wxid_xxxxxx",
    "file": "C:\\a.mp4"
  },
  "type": 11042
}
 

 ```









# 发送gif动图


##### 请求

 ``` 
 {
  "data": {
    "to_wxid": "wxid_xxxxxx",
    "file": "C:\\a.gif"
  },
  "type": 11043
}
 

 ```

# 语音转文本

##### 请求

 ``` 
 {
  "data": {
    "msgid": "448314506580xxx339"
  },
  "type": 11112
}
 

 ```


##### 返回示例 

``` 
 {
  "data": {
    "from_wxid": "xxxxx",
    "msgid": "448314xxxxxxx39",
    "room_wxid": "",
    "status": 1,
    "text": "测试语音，转文本功能。",
    "to_wxid": "wxid_xxxx2",
    "wx_type": 34
  },
  "type": 11112
}

```






# 小程序获取code


##### 请求

 ``` 
 {
  "type": 11136,
  "data": {
    "appid": "wxc37319241dff9766"
  }
}
 

 ```


##### 返回示例 

``` 
 {
  "data": {
    "appIconUrl": "",
    "appName": "",
    "baseResponse": {
      "errMsg": {
        "string": ""
      },
      "ret": 0
    },
    "code": "033uDvFa1wI2DD0ouJFaxxxxDvFm",
    "jsApiBaseResponse": {
      "errcode": 0,
      "errmsg": "ok"
    },
    "liftSpan": 0,
    "openId": "",
    "scopeList": [],
    "sessionKey": "",
    "sessionTicket": "",
    "signature": "",
    "state": ""
  },
  "type": 11136
}

```





# 朋友圈

## 获取朋友圈
##### 请求

 ``` 
 {
  "type": 11145,
  "data": {
    "max_id": "0"
  }
}
 

 ```
> max_id用于翻页
此api调用频率建议60s以上可配合翻页避免漏数据

##### 返回示例 

``` 
 {
  "data": {
    "advertiseCount": 0,
    "advertiseList": [],
    "baseResponse": {
      "errMsg": {},
      "ret": 0
    },
    "controlFlag": 2,
    "delAdvertiseCount": 0,
    "delAdvertiseList": [],
    "firstPageMd5": "67f612c1axxxx1b1e",
    "max_id": "1391966426949xxxx409",
    "newRequestTime": 1659359872,
    "objectCount": 10,
    "objectCountForSameMd5": 10,
    "objectList": [
      {
        "blackList": [],
        "blackListCount": 0,
        "commentCount": 1,

        // 评论
        "commentUserList": [
          {
            "commentFlag": 0,
            "commentId": 1,
            "commentId2": "0",
            "content": "所以说平台方只是割一波韭菜就跑，根本不管二级市场的价值",
            "createTime": 1659353338,
            "deleteFlag": 0,
            "isNotRichText": 1,
            "nickname": "r0ysue",
            "replyCommentId": 0,
            "replyCommentId2": "0",
            "replyUsername": "",
            "source": 0,
            "type": 2,
            "username": "wxid_986fxxxx2"
          }
        ],
        "commentUserListCount": 1,
        "createTime": 1659353288,
        "deleteFlag": 0,
        "extFlag": 1,
        "groupCount": 0,
        "groupList": [],
        "groupUser": [],
        "groupUserCount": 0,
        "id": "13919664269xxx9409",
        "isNotRichText": 1,
        "likeCount": 0,
        "likeFlag": 0,
        "likeUserList": [],
        "likeUserListCount": 0,
        "nickname": "r0ysue",
        "noChange": 0,
        "objectDesc": {
          "buffer": "PFRpbWVsaW5lT2JqZWN0PjxpZD48IVxxx=",  // 朋友圈内容，base64格式
          "iLen": 1630
        },
        "objectOperations": {
          "buffer": "CAA=",
          "iLen": 2
        },
        "preDownloadInfo": {
          "noPreDownloadRange": "",
          "preDownloadNetType": 0,
          "preDownloadPercent": 0
        },
        "referId": "0",
        "referUsername": "",
        "snsRedEnvelops": {
          "reportId": 0,
          "reportKey": 0,
          "resourceId": 0,
          "rewardCount": 0,
          "rewardUserList": []
        },
        "username": "wxid_986fyr2bxxxx22",
        "weAppInfo": {
          "appId": 0,
          "mapPoiId": "",
          "redirectUrl": "",
          "score": 0,
          "showType": 0,
          "userName": ""
        },
        "withUserCount": 0,
        "withUserList": [],
        "withUserListCount": 0
      }
    ],
    "recCount": 0,
    "recList": [],
    "serverConfig": {
      "copyAndPasteWordLimit": 100,
      "postMentionLimit": 10
    },
    "session": {
      "buffer":xxxLyln5cGKhoI4aGIgPWcpZbBARDiobiw5MH0lMEBGAogAVC8pZ+XBg==",
      "iLen": 70
    }
  },
  "type": 11145
}

```

## 获取还有朋友圈

##### 请求

 ``` 
 {
  "type": 11150,
  "data": {
    "username": "liamwu",
    "first_page_md5": "",
    "max_id": "0"
  }
}
 

 ```
> max_id用于翻页

##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "errMsg": {},
      "ret": 207
    },
    "continueId": "0",
    "firstPageMd5": "d639b742xxxb905",
    "limitedId": "0",
    "newRequestTime": 1659360077,
    "objectCount": 0,
    "objectCountForSameMd5": 0,
    "objectList": [],
    "objectTotalCount": 5,
    "retTips": "朋友仅展示最近三天的朋友圈",
    "serverConfig": {
      "copyAndPasteWordLimit": 100,
      "postMentionLimit": 10
    },
    "snsUserInfo": {
      "snsBgImgId": "http://szmmsns.qpic.cn/mmsns/ah7920oTxxxxxg/0",
      "snsBgObjectId": "136817xxxxx372928",
      "snsFlag": 1,
      "snsFlagEx": 7297
    }
  },
  "type": 11150
}

```






## 点赞

##### 请求

 ``` 
 {
  "data": {
    "object_id": "13834631000xxxxxxx"
  },
  "type": 11147
}
 

 ```
`说明: object_id从获取朋友圈返回值中，json路径： data.objectList[0].id`




##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "errMsg": {},
      "ret": 0
    },
    "snsObject": {
      "blackList": [],
      "blackListCount": 0,
      "commentCount": 1,
      "commentUserList": [
        {
          "commentFlag": 0,
          "commentId": 707,
          "commentId2": "0",
          "content": "666",
          "createTime": 1649220886,
          "deleteFlag": 0,
          "isNotRichText": 1,
          "nickname": "你看上去很好吃",
          "replyCommentId": 0,
          "replyCommentId2": "0",
          "replyUsername": "",
          "source": 0,
          "type": 2,
          "username": "wxid_d7zqxxxr22"
        }
      ],
      "commentUserListCount": 1,
      "createTime": 1649216532,
      "deleteFlag": 0,
      "extFlag": 1,
      "groupCount": 0,
      "groupList": [],
      "groupUser": [],
      "groupUserCount": 0,
      "id": "13834631000908697847",
      "isNotRichText": 1,
      "likeCount": 1,
      "likeFlag": 1,
      "likeUserList": [
        {
          "commentFlag": 0,
          "commentId": 0,
          "commentId2": "0",
          "content": "",
          "createTime": 1649217543,
          "deleteFlag": 0,
          "isNotRichText": 0,
          "nickname": "你xxxxx",
          "replyCommentId": 0,
          "replyCommentId2": "0",
          "replyUsername": "",
          "source": 0,
          "type": 1,
          "username": "wxid_dxxk6xxxxxx22"
        }
      ],
      "likeUserListCount": 1,
      "nickname": "r0ysue",
      "noChange": 0,
      "objectDesc": {
        "buffer": "PFRpbWVxWNx",
        "iLen": 2035
      },
      "objectOperations": {
        "buffer": "CAA=",
        "iLen": 2
      },
      "preDownloadInfo": {
        "noPreDownloadRange": "",
        "preDownloadNetType": 0,
        "preDownloadPercent": 0
      },
      "referId": "0",
      "referUsername": "",
      "snsRedEnvelops": {
        "reportId": 0,
        "reportKey": 0,
        "resourceId": 0,
        "rewardCount": 0,
        "rewardUserList": []
      },
      "username": "wxid_986fyxxxx22",
      "weAppInfo": {
        "appId": 0,
        "mapPoiId": "",
        "redirectUrl": "",
        "score": 0,
        "showType": 0,
        "userName": ""
      },
      "withUserCount": 0,
      "withUserList": [],
      "withUserListCount": 0
    }
  },
  "type": 11147
}

```






## 评论

##### 请求

 ``` 
 {
  "data": {
    "object_id": "13834631000908697847",
    "content": "666"
  },
  "type": 11146
}
 

 ```

`说明: object_id从获取朋友圈返回值中，json路径： data.objectList[0].id`



##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "errMsg": {},
      "ret": 0
    },
    "snsObject": {
      "blackList": [],
      "blackListCount": 0,
      "commentCount": 2,
      "commentUserList": [
        {
          "commentFlag": 0,
          "commentId": 707,
          "commentId2": "0",
          "content": "666",
          "createTime": 1649220886,
          "deleteFlag": 0,
          "isNotRichText": 1,
          "nickname": "你看上xxxxxx",
          "replyCommentId": 0,
          "replyCommentId2": "0",
          "replyUsername": "",
          "source": 0,
          "type": 2,
          "username": "wxid_d7xxxxxxx2"
        }
      ],
      "commentUserListCount": 2,
      "createTime": 1649216532,
      "deleteFlag": 0,
      "extFlag": 1,
      "groupCount": 0,
      "groupList": [],
      "groupUser": [],
      "groupUserCount": 0,
      "id": "13834631000908697847",
      "isNotRichText": 1,
      "likeCount": 1,
      "likeFlag": 1,
      "likeUserList": [
        {
          "commentFlag": 0,
          "commentId": 0,
          "commentId2": "0",
          "content": "",
          "createTime": 1649217543,
          "deleteFlag": 0,
          "isNotRichText": 0,
          "nickname": "xxxxxxx",
          "replyCommentId": 0,
          "replyCommentId2": "0",
          "replyUsername": "",
          "source": 0,
          "type": 1,
          "username": "wxid_d7zxxxx2"
        }
      ],
      "likeUserListCount": 1,
      "nickname": "r0ysue",
      "noChange": 0,
      "objectDesc": {
        "buffer": "PFRpbWVsaW5lT2JqZWN0PjxpZDxxxxOYW1lPjxwYWdlUGF0aD48L3BhZ2VQYXRoPjx2ZXJzaW9uPjwhW0NEQVRBWzBdXT48L3ZlcnNpb24+PGRlYnVnTW9kZT48IVtDREFUQVswXV0xxx=",
        "iLen": 2035
      },
      "objectOperations": {
        "buffer": "CAA=",
        "iLen": 2
      },
      "preDownloadInfo": {
        "noPreDownloadRange": "",
        "preDownloadNetType": 0,
        "preDownloadPercent": 0
      },
      "referId": "0",
      "referUsername": "",
      "snsRedEnvelops": {
        "reportId": 0,
        "reportKey": 0,
        "resourceId": 0,
        "rewardCount": 0,
        "rewardUserList": []
      },
      "username": "wxid_98xx22",
      "weAppInfo": {
        "appId": 0,
        "mapPoiId": "",
        "redirectUrl": "",
        "score": 0,
        "showType": 0,
        "userName": ""
      },
      "withUserCount": 0,
      "withUserList": [],
      "withUserListCount": 0
    }
  },
  "type": 11146
}

```






## 上传图片

##### 请求

 ``` 
 {
  "type": 11149,
  "data": {
    "path": "c:\\Users\\evilbeast\\Pictures\\Aurora-1080.jpg"
  }
}
 

 ```
`会返回多次只有startPos和totalLen相同时，才是上传结束`




##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "ret": 0
    },
    "bufferUrl": {
      "type": 1,
      "url": "http://mmsns.qpic.cn/mmsns/PiajxSqBRaELLlixxxxxPiatavbWSuVfPbl/0"
    },
    "clientId": "wxid_xxxxxxx4758",
    "id": 0,
    "startPos": 3554200,
    "thumbUrlCount": 1,
    "thumbUrls": [
      {
        "type": 1,
        "url": "http://mmsns.qpic.cn/mmsns/PiajxSxxxxxfPbl/150"
      }
    ],
    "totalLen": 3554200,
    "type": 2
  },
  "type": 11149
}

```






## 发朋友圈

##### 请求

 ``` 
 {
  "type": 11148,
  "data": {
    "object_desc": "<TimelineObject><id>0</id><username></username><createTime>1625847261</createTime><contentDesc>666[破涕为笑]</contentDesc><contentDescShowType>0</contentDescShowType><contentDescScene>3</contentDescScene><private>0</private><sightFolded>0</sightFolded><showFlag>0</showFlag><appInfo><id></id><version></version><appName></appName><installUrl></installUrl><fromUrl></fromUrl><isForceUpdate>0</isForceUpdate></appInfo><sourceUserName></sourceUserName><sourceNickName></sourceNickName><statisticsData></statisticsData><statExtStr></statExtStr><ContentObject><contentStyle>2</contentStyle><title></title><description></description><mediaList></mediaList><contentUrl></contentUrl></ContentObject><actionInfo><appMsg><messageAction></messageAction></appMsg></actionInfo><location poiClassifyId=\"\" poiName=\"\" poiAddress=\"\" poiClassifyType=\"0\" city=\"\"></location><publicUserName></publicUserName><streamvideo><streamvideourl></streamvideourl><streamvideothumburl></streamvideothumburl><streamvideoweburl></streamvideoweburl></streamvideo></TimelineObject>"
  }
}
 

 ```
`说明oject_desc可以从朋友圈返回结果中取,json路径: data.objectList[0].objectDesc, 结果需要base64解码， 其中username换成自己的wxid`

##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "errMsg": {},
      "ret": 0
    },
    "snsObject": {
      "blackList": [],
      "blackListCount": 0,
      "commentCount": 0,
      "commentUserList": [],
      "commentUserListCount": 0,
      "createTime": 1659360780,
      "deleteFlag": 0,
      "extFlag": 1,
      "groupCount": 0,
      "groupList": [],
      "groupUser": [],
      "groupUserCount": 0,
      "id": "1391972xxxxxxx2",  // 朋友圈id
      "isNotRichText": 0,
      "likeCount": 0,
      "likeFlag": 0,
      "likeUserList": [],
      "likeUserListCount": 0,
      "nickname": "xxxxxxxx",
      "noChange": 0,
      "objectDesc": {
        "buffer": "<TimelineObject>...</TimelineObject>",
        "iLen": 1089
      },
      "objectOperations": {
        "buffer": "",
        "iLen": 0
      },
      "preDownloadInfo": {
        "noPreDownloadRange": "",
        "preDownloadNetType": 0,
        "preDownloadPercent": 0
      },
      "referId": "0",
      "referUsername": "",
      "snsRedEnvelops": {
        "reportId": 0,
        "reportKey": 0,
        "resourceId": 0,
        "rewardCount": 0,
        "rewardUserList": []
      },
      "username": "wxid_dxxkxxxxx2",
      "weAppInfo": {
        "appId": 0,
        "mapPoiId": "",
        "redirectUrl": "",
        "score": 0,
        "showType": 0,
        "userName": ""
      },
      "withUserCount": 0,
      "withUserList": [],
      "withUserListCount": 0
    },
    "spamTips": ""
  },
  "type": 11148
}

```






# A8key

##### 请求

 ``` 
 {
  "type": 11135,
  "data": {
    "url": "https://support.weixin.qq.com/cgi-bin/mmsupport-bin/addchatroombyinvite?ticket=AQwfsFUEysZWaraDbl8AQA%3D%3D",
    "scene": 1
  }
}
 

 ```


##### 返回示例 

``` 
 {
  "data": {
    "a8Key": "",
    "actionCode": 6,
    "antispamTicket": "",
    "baseResponse": {
      "errMsg": {},
      "ret": 0
    },
    "content": "",
    "cookie": {
      "buffer": "CMCBnpcG",
      "iLen": 6
    },
    "deepLinkBitSet": {
      "bitValue": "18014398777982976"
    },
    "fullUrl": "https://szsupport.weixin.qq.com/cgi-bin/mmsupport-bin/addchatroombyinvite?ticket=AQwfsFUEysZWaraDbl8AQA%3D%3D&exportkey=n_ChQIAhIQKZVfMlf41UUtRMTfMhCGrhLLAQIE97dBBAEAAAAAANPZBEL7fT8AAAAOpnltbLcz9gKNyK89dVj04%2BShQboU34zPeBh4BoMQ6k%2Br4mSfV1%2BwuqttEQmokQJP4R3b7j1mCX%2Bw3t1K2EaSdiyTIuBJeP%2FZ%2BTJzF3EdBO0MYD0WH%2FfbSvt2Hppcl2Zx5XtXFoHEWrv7uyP80kwoCMAY0XtMoBqj0OBJcXLn41ol4n6%2BD3dSD%2BnyvvjiYrsPNWSw%2FRWOr26pamUgxixuXDaDiSOI%2BrTscu%2BrDWUKnHAhdnJ1&lang=zh_CN&hashuin=334958563&pass_ticket=fEICZBrRpM4%2FkzBysKEKO7Drz9ao2vYO3r57jNx%2B8Jr6dxIls8Qm4kZhLsqXOMTN&wechat_real_lang=zh_CN&wx_header=0",
    "generalControlBitSet": {
      "bitValue": 1101914
    },
    "headImg": "",
    "httpHeaderCount": 1,
    "httpHeaderList": [
      {
        "key": "exportkey",
        "value": "n_ChQIAhIQKZVfMlf41Uxxxx"
      }
    ],
    "jsapicontrolBytes": {
      "buffer": "AQEBAgEDAQECAgIBAQExxxxx",
      "iLen": 468
    },
    "jsapipermission": {
      "bitValue": 1941960919,
      "bitValue2": 826277849,
      "bitValue3": 1803948116,
      "bitValue4": 131840
    },
    "menuWording": "",
    "mid": "",
    "scopeCount": 0,
    "scopeList": [],
    "shareUrl": "https://support.weixin.qq.com/cgi-bin/mmsupport-bin/addchatroombyinvite?ticket=AQwfsFUEysZWaraDbl8AQA%3D%3D",
    "ssid": "",
    "title": "",
    "userName": "",
    "wording": ""
  },
  "type": 11135
}

```





# 登录
## 接口就绪通知

##### 通知示例
> 当接口就绪后的通知，是最早的一个通知
 
 ``` 
 {
  "data": {
    "pid": 27620
  },
  "type": 11024
}
 

 ```








## 用户登录成功

##### 通知示例

从扫码登录或已经登录的微信都会触发这个消息
 ``` 
 {
  "data": {
    "account": "12222222",
    "avatar": "http://wx.qlogo.cn/mmhead/ver_1/GSaaxx0/132",
    "nickname": "昵称",
    "phone": "xxxxxxxxxxxx",
    "pid": 40808,
    "unread_msg_count": 0,
    "wx_user_dir": "C:\\Users\\evilbeast\\Documents\\WeChat Files\\wxid_dxxxxxxx2\\",
    "wxid": "wxid_dxxxxxxx2"
  },
  "type": 11025
}

 

 ```









# 通知

## 文本消息
##### JOSN示例

 ``` 
{
   "data" : {
      "at_user_list" : ["wxid_xxxxxxx", "wxid_xxxxxxxx"],
      "from_wxid" : "xxxxxxxxx",
      "msg" : "你好，世界",
      "room_wxid" : "xxxxxx@chatroom",
      "to_wxid" : "wxid_xxxxxxxx",
      "wx_type" : 1
   },
   "type" : 11046
}



 

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`

## 图片消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "image" : "C:\\xxx\\3f5ac25836d60fe4b43a83ab90b7c245.dat",
      "image_thumb" : "C:\\xxxx\956eb5a6cf0d5e1f402553a65b89333_t.dat",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 3
   },
   "type" : 11047
}


 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`








## 语音消息



##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "slik_file" : "c://xxxx.slik",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 3
   },
   "type" : 11048
}

 

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`







## 名片消息



##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 42
   },
   "type" : 11050
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送
`
`room_wxid 判断这个字段区分是否为群聊消息`

`名片消息中raw_msg包含wxid或encryptusername数据，可以使用加分享名片接口添加好友`










## 视频消息



##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "video" : "C:\\xxx\\3f5ac25836d60fe4b43a83ab90b7c245.mp4",
      "video_thumb" : "C:\\xxxx\3f5ac25836d60fe4b43a83ab90b7c245.jpg",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 43
   },
   "type" : 11051
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送
`
`room_wxid 判断这个字段区分是否为群聊消息`







## 表情消息



##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 47
   },
   "type" : 11052
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送
`
`room_wxid 判断这个字段区分是否为群聊消息`







## 位置消息



##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 48
   },
   "type" : 11053
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`




## 系统消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "xxxxx",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 10000
   },
   "type" : 11058
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`

`无需对系统通知做自动回复`  
eg:  
1.发消息-被对方拉黑之后,raw\_msg 为”消息已发出，但被对方拒收了”  
2.有红包出没时:”发出红包，请在手机上查看”  
3.修改群名称后:xxxxx修改群名为xxxxxxx  
其他:

*   群主已恢复默认进群方式。
*   群主已启用“群聊邀请确认”，群成员需群主确认才能邀请朋友进群。
*   你已成为新群主
*   xxxxxx已成为新群主
*   你邀请xxxx加入了群聊
*   xxxx邀请xxxx加入了群聊
*   xxxxx通过扫描你分享的二维码加入群聊”
*   xxxxx通过扫描xxxxxx分享的二维码加入群聊”



## 撤回消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "xxxxxx",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 10002
   },
   "type" : 11059
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`








## 其他消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : x
   },
   "type" : 11060
}


 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`








## 链接消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 49,
      "wx_sub_type" : 5
   },
   "type" : 11048
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`

`链接的URL包含addchatroombyinvite，就是群邀请消息，可以向接口发送自动同意进群`


## 文件消息


##### JOSN示例
|  参数 | 类型  |描述   |
| ------------ | ------------ | ------------ |
| from_wxid  |  string | 发送者的wxid  |
|  raw_msg |  string |  微信中的原始消息,xml格式 |
| room_wxid  |  string |  群聊的wxid |
|  to_wxid |  string |  接收者的wxid |
| file  |  string | 文件的路径  |
| wx_type  |   number| 微信原始类型，值为49  |
|  wx_sub_type |   number|  微信原始应用子类型，值为6 |


 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 49,
      "wx_sub_type" : 6
   },
   "type" : 11055
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`








## 小程序消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 49,
      "wx_sub_type" : 33
   },
   "type" : 11056
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`







## 转账消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 49,
      "wx_sub_type" : 2000
   },
   "type" : 11057
}

 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`

`raw_msg包含转账信息，可以发送接收转账接口，也可以向接口设置自动接收转帐`







## 其他应用消息


##### JOSN示例

 ``` 
{
   "data" : {
      "from_wxid" : "xxxxxxx",
      "raw_msg" : "<?xml version=\"1.0\"?>\n<msg>xxxxxx</msg>",
      "room_wxid" : "",
      "to_wxid" : "wxid_xxxxxx",
      "wx_type" : 49,
      "wx_sub_type" : x
   },
   "type" : 11061
}


 ```
`from_wxid也包含是自己的情况，使用时注意判断，避免循环发送`

`room_wxid 判断这个字段区分是否为群聊消息`








# 群聊
## 群成员新增


##### JOSN示例

 ``` 
{
    "data": {
        "avatar": "",
        "is_manager": 1,
        "manager_wxid": "wxid_5cg2ixxxx",
        "member_list": [{
            "nickname": "Mr.Name",
            "wxid": "wxid_xxxx",
            "invite_by": "wxid_xxxx", // 邀请人wxid
        }],
        "nickname": "测试群名",     
        "room_wxid": "223xxxx7@chatroom",
        "total_member": 5
    },
    "type": 11098
}

 ```









## 群成员删除


##### JOSN示例

 ``` 
{
    "data": {
        "avatar": "",
        "is_manager": 1,
        "manager_wxid": "wxid_5cgxxr22",
        "member_list": [{
            "nickname": "移出的群成员昵称",
            "wxid": "wxid_uccccc1"
        }],
        "nickname": "测试群名",
        "room_wxid": "22xxxx3417@chatroom",
        "total_member": 4
    },
    "type": 11099
}

 ```









## 群创建成功


##### JOSN示例

 ``` 
{
    "data": {
        "avatar": "",
        "is_manager": 1,
        "manager_wxid": "wxid_5cxxxx2",
        "member_list": [{
            "nickname": "群成员昵称",
            "wxid": "wxid_uccccc1"
        }],
        "nickname": "测试群名",
        "room_wxid": "2238xxx17@chatroom",
        "total_member": 4
    },
    "type": 11100
}

 ```









## 退群 被踢


##### JOSN示例

 ``` 
{
    "data": {
        "room_wxid": "232xxxxx@chatroom"
    },
    "type": 11101
}


 ```









# 窗口句柄变化


##### JOSN示例

 ``` 
 {
  "data": {
    "login_hwnd": 2563030,
    "login_shadow_hwnd": 2953226,
    "main_hwnd": 0,
    "main_shadow_hwnd": 0,
    "pid": 40808
  },
  "type": 11088
}

 ```
> 当登录窗口和主窗口句柄变化时的通知








# 聊天对象变化


##### JOSN示例

 ``` 
{
    "data": {
        "status": 1,
        "user": {
            "avatar": "http://wx.qlogo.cn/xxxx",
            "is_manager": 1,
            "manager_wxid": "wxid_5xxxxx",
            "nickname": "测试群聊123",
            "total_member": 4,
            "wxid": "xxxxx@chatroom"
        },
        "user_type": 2    // 1为好友， 2为群， 3为不可聊天的公众号
    },
    "type": 11091
}


 ```


##### 请求

 ``` 
 {
  "data": {
    "client_msgid": 165899xx84,
    "create_time": 1658991484,
    "to_wxid": "xxxxxxxx",
    "new_msgid": "86028099xxx2641"
  },
  "type": 11244
}


 ```


##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "ret": 0
    },
    "introduction": "",
    "sysWording": "已撤回"
  },
  "type": 11244
}

```






##### 请求

 ``` 
 {
  "data": {
    "client_msgid": 165899xx84,
    "create_time": 1658991484,
    "to_wxid": "xxxxxxxx",
    "new_msgid": "86028099xxx2641"
  },
  "type": 11244
}


 ```


##### 返回示例 

``` 
 {
  "data": {
    "baseResponse": {
      "ret": 0
    },
    "introduction": "",
    "sysWording": "已撤回"
  },
  "type": 11244
}

```







##### 请求

 ``` 
 {
  "type": 11214,
  "data": {
    "content": "<appmsg appid=\"wx6618f1cfc6c132f8\" sdkver=\"0\"><title>1658934822522.gif</title><des></des><action>view</action><type>8</type><showtype>0</showtype><content></content><url></url><dataurl></dataurl><lowurl></lowurl><lowdataurl></lowdataurl><recorditem></recorditem><thumburl></thumburl><messageaction></messageaction><md5>a2ed2d6159ad865c93ba25db74df4616</md5><extinfo></extinfo><sourceusername></sourceusername><sourcedisplayname></sourcedisplayname><commenturl></commenturl><appattach><totallen>393652</totallen><attachid></attachid><emoticonmd5>a2ed2d6159ad865c93ba25db74df4616</emoticonmd5><fileext>gif</fileext><cdnattachurl>3057020100044b3049020100020462ac90a202032df4d9020428fb131b020462e15ab0042435306239613864642d626562392d343535612d383663662d6431666335373935396266630204011400050201000405004c4c6d00</cdnattachurl><aeskey>24b6123e04d1d08702143658917c85e7</aeskey></appattach><weappinfo><pagepath></pagepath><username></username><appid></appid><appservicetype>0</appservicetype></weappinfo><websearch /></appmsg>",
    "to_wxid": "filehelper"
  }
}
 

 ```


##### 返回示例 

``` 
 {
  "data": {
    "actionFlag": 0,
    "aeskey": "",
    "appId": "",
    "baseResponse": {
      "ret": 0
    },
    "clientMsgId": "468707f88f3xxxxcf3e97b",
    "createTime": 1658987693,
    "fromUserName": "wxid_d7zqk6yxxx22",
    "msgId": 774252593,
    "msgSource": "<msgsource>\n\t<sec_msg_node>\n\t\t<uuid>2367d76ac736xxxa32a1_</uuid>\n\t</sec_msg_node>\n</msgsource>\n",
    "newMsgId": "8464116513xxx916",
    "toUserName": "filehelper",
    "type": 63
  },
  "type": 11214
}

```






