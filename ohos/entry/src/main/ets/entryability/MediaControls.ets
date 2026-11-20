import { avSession } from '@kit.AVSessionKit';
import { backgroundTaskManager } from '@kit.BackgroundTasksKit';
import { image } from '@kit.ImageKit';
import { UIAbility, WantAgent, wantAgent } from '@kit.AbilityKit';
import {
  Any,
  BinaryMessenger,
  Log,
  MethodCall,
  MethodCallHandler,
  MethodChannel,
  MethodResult,
} from '@ohos/flutter_ohos';

const TAG: string = 'MediaControls';

/**
 * 鸿蒙侧 duanju/media 通道实现：
 * AVSession 播控中心/锁屏媒体卡片 + AUDIO_PLAYBACK 长时任务保活。
 * 协议与 Android 端 MediaControls.kt 对齐。
 */
export class MediaControls implements MethodCallHandler {
  private channel: MethodChannel;
  private ability: UIAbility;
  private session: avSession.AVSession | null = null;
  private creating: boolean = false;
  private coverPath: string = '';
  private cover: image.PixelMap | null = null;

  private lastPositionMs: number = 0;
  private running: boolean = false;

  constructor(messenger: BinaryMessenger, ability: UIAbility) {
    this.ability = ability;
    this.channel = new MethodChannel(messenger, 'duanju/media');
    this.channel.setMethodCallHandler(this);
  }

  onMethodCall(call: MethodCall, result: MethodResult): void {
    switch (call.method) {
      case 'update':
        this.update(call);
        result.success(null);
        break;
      case 'clear':
        this.clear();
        result.success(null);
        break;
      default:
        result.notImplemented();
        break;
    }
  }

  private async update(call: MethodCall): Promise<void> {
    const title: string = MediaControls.text(call.argument('title'));
    const subtitle: string = MediaControls.text(call.argument('subtitle'));
    const artwork: string = MediaControls.text(call.argument('artwork'));
    const active: boolean = call.argument('active') === true;
    const playing: boolean = call.argument('playing') === true;
    const positionMs: number = MediaControls.number(call.argument('positionMs'));
    const durationMs: number = MediaControls.number(call.argument('durationMs'));
    const rate: number = MediaControls.number(call.argument('rate'));
    const controlsWanted: boolean = call.argument('controls') !== false;
    const backgroundWanted: boolean = call.argument('background') !== false;
    if (positionMs > 0) {
      this.lastPositionMs = positionMs;
    }
    if (controlsWanted && active) {
      const session = await this.ensureSession();
      if (session != null) {
        try {
          if (artwork !== this.coverPath) {
            this.coverPath = artwork;
            await this.loadCover(artwork);
          }
          const metadata: avSession.AVMetadata = {
            assetId: `${title}|${subtitle}`,
            title: title,
            subtitle: subtitle,
            artist: subtitle,
            duration: durationMs,
          };
          if (this.cover != null) {
            metadata.mediaImage = this.cover;
          }
          await session.setAVMetadata(metadata);
          const playbackState: avSession.AVPlaybackState = {
            state: playing
              ? avSession.PlaybackState.PLAYBACK_STATE_PLAY
              : avSession.PlaybackState.PLAYBACK_STATE_PAUSE,
            speed: rate > 0 ? rate : 1,
            position: {
              elapsedTime: positionMs,
              updateTime: Date.now(),
            },
          };
          await session.setAVPlaybackState(playbackState);
          await session.activate();
        } catch (error) {
          Log.e(TAG, `update failed: ${JSON.stringify(error)}`);
        }
      }
    } else {
      await this.deactivateSession();
    }
    const sessionActive: boolean = controlsWanted && active && this.session != null;
    if (sessionActive && backgroundWanted) {
      await this.startTask();
    } else {
      await this.stopTask();
    }
  }

  private async ensureSession(): Promise<avSession.AVSession | null> {
    if (this.session != null) {
      return this.session;
    }
    if (this.creating) {
      return null;
    }
    this.creating = true;
    try {
      const session = await avSession.createAVSession(
        this.ability.context,
        'guoapp',
        'audio',
      );
      this.registerCommands(session);
      await session.setLaunchAbility(await this.buildAgent());
      this.session = session;
      Log.i(TAG, `AVSession created: ${session.sessionId}`);
    } catch (error) {
      Log.e(TAG, `createAVSession failed: ${JSON.stringify(error)}`);
    }
    this.creating = false;
    return this.session;
  }

  private registerCommands(session: avSession.AVSession): void {
    session.on('play', () => this.send('play'));
    session.on('pause', () => this.send('pause'));
    session.on('stop', () => this.send('pause'));
    session.on('playNext', () => this.send('next'));
    session.on('playPrevious', () => this.send('previous'));
    session.on('seek', (time: number) => this.send('seek', time));
  }

  private async buildAgent(): Promise<WantAgent> {
    const context = this.ability.context;
    const info: wantAgent.WantAgentInfo = {
      wants: [
        {
          bundleName: context.abilityInfo.bundleName,
          abilityName: context.abilityInfo.name,
        },
      ],
      operationType: wantAgent.OperationType.START_ABILITY,
      requestCode: 0,
      wantAgentFlags: [wantAgent.WantAgentFlags.UPDATE_PRESENT_FLAG],
    };
    return wantAgent.getWantAgent(info);
  }

  private async startTask(): Promise<void> {
    if (this.running) {
      return;
    }
    this.running = true;
    try {
      await backgroundTaskManager.startBackgroundRunning(
        this.ability.context,
        backgroundTaskManager.BackgroundMode.AUDIO_PLAYBACK,
        await this.buildAgent(),
      );
      Log.i(TAG, 'background running started');
    } catch (error) {
      this.running = false;
      Log.e(TAG, `startBackgroundRunning failed: ${JSON.stringify(error)}`);
    }
  }

  private async stopTask(): Promise<void> {
    if (!this.running) {
      return;
    }
    this.running = false;
    try {
      await backgroundTaskManager.stopBackgroundRunning(this.ability.context);
      Log.i(TAG, 'background running stopped');
    } catch (error) {
      Log.e(TAG, `stopBackgroundRunning failed: ${JSON.stringify(error)}`);
    }
  }

  private async loadCover(path: string): Promise<void> {
    const previous = this.cover;
    this.cover = null;
    if (previous != null) {
      previous.release();
    }
    if (path.length === 0) {
      return;
    }
    try {
      let realPath: string = path;
      if (realPath.startsWith('file://')) {
        realPath = realPath.substring('file://'.length);
      }
      const source: image.ImageSource = image.createImageSource(realPath);
      const pixelMap = await source.createPixelMap();
      await source.release();
      this.cover = pixelMap;
    } catch (error) {
      Log.e(TAG, `loadCover failed: ${JSON.stringify(error)}`);
    }
  }

  private async deactivateSession(): Promise<void> {
    const session = this.session;
    if (session == null) {
      return;
    }
    try {
      const pausedState: avSession.AVPlaybackState = {
        state: avSession.PlaybackState.PLAYBACK_STATE_PAUSE,
        position: {
          elapsedTime: this.lastPositionMs,
          updateTime: Date.now(),
        },
      };
      await session.setAVPlaybackState(pausedState);
    } catch (error) {
      Log.e(TAG, `pause state failed: ${JSON.stringify(error)}`);
    }
    try {
      await session.deactivate();
    } catch (error) {
      Log.e(TAG, `deactivate failed: ${JSON.stringify(error)}`);
    }
  }

  private async clear(): Promise<void> {
    const session = this.session;
    this.session = null;
    this.coverPath = '';
    await this.stopTask();
    const cover = this.cover;
    this.cover = null;
    if (cover != null) {
      cover.release();
    }
    if (session == null) {
      return;
    }
    try {
      await session.deactivate();
      await session.destroy();
      Log.i(TAG, 'AVSession destroyed');
    } catch (error) {
      Log.e(TAG, `clear failed: ${JSON.stringify(error)}`);
    }
  }

  private send(command: string, positionMs?: number): void {
    const args: Map<string, Object> = new Map();
    args.set('command', command);
    if (positionMs != null) {
      args.set('positionMs', positionMs);
    }
    this.channel.invokeMethod('command', args);
  }

  private static text(value: Any): string {
    if (typeof value === 'string') {
      return value;
    }
    return '';
  }

  private static number(value: Any): number {
    if (typeof value === 'number') {
      return value;
    }
    return 0;
  }
}
