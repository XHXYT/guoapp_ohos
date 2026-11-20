
import { FlutterAbility, FlutterEngine } from '@ohos/flutter_ohos';
import { GeneratedPluginRegistrant } from '../plugins/GeneratedPluginRegistrant';
import { MediaControls } from './MediaControls';

export default class EntryAbility extends FlutterAbility {
  private mediaControls: MediaControls | null = null;

  configureFlutterEngine(flutterEngine: FlutterEngine) {
    super.configureFlutterEngine(flutterEngine)
    GeneratedPluginRegistrant.registerWith(flutterEngine)
    if (this.mediaControls == null) {
      this.mediaControls = new MediaControls(flutterEngine.dartExecutor, this)
    }
  }
}
