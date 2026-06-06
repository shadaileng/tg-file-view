import { describe, it, expect } from 'vitest'
import { authApi, channelsApi, filesApi, syncApi, thumbnailsApi, cacheApi, configApi, healthApi } from '../api/index'

describe('API 模块', () => {
  it('authApi 端点正确', () => {
    expect(authApi.sendCode).toBeDefined()
    expect(authApi.verifyCode).toBeDefined()
    expect(authApi.verify2FA).toBeDefined()
    expect(authApi.status).toBeDefined()
    expect(authApi.logout).toBeDefined()
  })

  it('channelsApi 端点正确', () => {
    expect(channelsApi.list).toBeDefined()
    expect(channelsApi.get).toBeDefined()
    expect(channelsApi.create).toBeDefined()
    expect(channelsApi.delete).toBeDefined()
    expect(channelsApi.discover).toBeDefined()
  })

  it('filesApi 端点正确', () => {
    expect(filesApi.list).toBeDefined()
    expect(filesApi.get).toBeDefined()
    expect(filesApi.download).toBeDefined()
    expect(filesApi.view).toBeDefined()
    expect(filesApi.cache).toBeDefined()
    expect(filesApi.deleteCache).toBeDefined()
  })

  it('syncApi 端点正确', () => {
    expect(syncApi.trigger).toBeDefined()
    expect(syncApi.listTasks).toBeDefined()
    expect(syncApi.getTask).toBeDefined()
    expect(syncApi.cancel).toBeDefined()
  })

  it('thumbnailsApi 端点正确', () => {
    expect(thumbnailsApi.generateSingle).toBeDefined()
    expect(thumbnailsApi.generateBatch).toBeDefined()
    expect(thumbnailsApi.listJobs).toBeDefined()
    expect(thumbnailsApi.getJob).toBeDefined()
    expect(thumbnailsApi.stats).toBeDefined()
    expect(thumbnailsApi.cancel).toBeDefined()
  })

  it('cacheApi 端点正确', () => {
    expect(cacheApi.stats).toBeDefined()
    expect(cacheApi.evict).toBeDefined()
  })

  it('configApi 端点正确', () => {
    expect(configApi.list).toBeDefined()
    expect(configApi.get).toBeDefined()
    expect(configApi.update).toBeDefined()
  })

  it('healthApi 端点正确', () => {
    expect(healthApi.check).toBeDefined()
  })
})
