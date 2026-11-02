import { shallowReactive } from 'vue'

export function createAgentTasks() {
  const controllers = shallowReactive({})
  return {
    controllers,
    start(code) {
      if (controllers[code]) throw new Error('当前咨询正在处理中')
      const controller = new AbortController()
      controllers[code] = controller
      return controller
    },
    finish(code, controller) { if (controllers[code] === controller) delete controllers[code] },
    stop(code) { controllers[code]?.abort() },
    stopAll() {
      Object.values(controllers).forEach(controller => controller.abort())
      Object.keys(controllers).forEach(code => delete controllers[code])
    },
  }
}
