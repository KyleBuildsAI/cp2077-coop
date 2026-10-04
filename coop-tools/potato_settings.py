"""Apply a low-GPU "potato" profile to a COPY of Cyberpunk 2077 2.31a UserSettings.json.

Usage:
    python potato_settings.py <source.json> <dest.json> [--fps 60] [--res 1280x720] [--upscaler off|dlss_perf]

The script refuses to write to the live file in %LOCALAPPDATA%\\CD Projekt Red\\Cyberpunk 2077.
Swapping the result in (and restoring the backup afterwards) is a separate, deliberate step that the
user runs with both game instances closed, for example in PowerShell:
    $dir = "$env:LOCALAPPDATA\\CD Projekt Red\\Cyberpunk 2077"
    Copy-Item "$dir\\UserSettings.json" "$dir\\UserSettings.json.coopbak"            # backup once
    python potato_settings.py "$dir\\UserSettings.json.coopbak" .\\UserSettings.potato.json
    Copy-Item .\\UserSettings.potato.json "$dir\\UserSettings.json"                 # test session
    Copy-Item "$dir\\UserSettings.json.coopbak" "$dir\\UserSettings.json"           # restore afterwards
"""
import argparse
import json
import os
import sys
from pathlib import Path

LIVE_SETTINGS = Path(os.environ.get('LOCALAPPDATA', '')) / 'CD Projekt Red' / 'Cyberpunk 2077' / 'UserSettings.json'

# Dynamic string_list options carry no "values" array in the file; their index is taken from the
# lists observed in this install's UserSettings files (2.31a v140 and the older v123 backup).
DYNAMIC_INDEX = {
    ('/video/display', 'WindowMode'): {'Windowed': 0, 'BorderlessWindowed': 1, 'Fullscreen': 2},
    ('/video/display', 'VSync'): {'UI-Settings-Video-QualitySetting-Off': 0},
    ('/graphics/presets', 'QuickPresets'): {'Custom': 0},
    ('/graphics/presets', 'ResolutionScaling'): {'Off': 0, 'DLSS': 1},
    ('/graphics/presets', 'DLSS'): {'Auto': 0, 'DLAA': 1, 'Quality': 2, 'Balanced': 3, 'Performance': 4,
                                    'Ultra Performance': 5},
    ('/graphics/presets', 'FrameGeneration'): {'Off': 0, 'DLSS': 1},
}

POTATO = {
    '/video/display': {
        'WindowMode': 'Windowed',
        'Resolution': None,  # filled from --res
        'VSync': 'UI-Settings-Video-QualitySetting-Off',
        'MaximumFPS_OnOff': True,
        'MaximumFPS_Value': None,  # filled from --fps
    },
    '/graphics/presets': {
        'QuickPresets': 'Custom',
        'ResolutionScaling': None,  # filled from --upscaler
        'DLSS_D': False,
        'DynamicResolutionScaling': False,
        'FrameGeneration': 'Off',
        'DLSSFrameGen': False,
        'FSR3_FrameGeneration': False,
        'XESS_FrameGeneration': False,
        'TextureQuality': 'Low',
    },
    '/graphics/raytracing': {
        'RayTracing': False,
        'RayTracedReflections': False,
        'RayTracedSunShadows': False,
        'RayTracedLocalShadows': False,
        'RayTracedLighting': 'Off',
        'RayTracedPathTracing': False,
        'RayTracedPathTracingForPhotoMode': False,
    },
    '/graphics/advanced': {
        'ContactShadows': False,
        'FacialTangentUpdates': False,
        'Anisotropy': 1,
        'ShadowMeshQuality': 'Low',
        'LocalShadowsQuality': 'Off',
        'CascadedShadowsRange': 'Low',
        'CascadedShadowsResolution': 'Low',
        'DistantShadowsResolution': 'Low',
        'VolumetricFogResolution': 'Low',
        'VolumetricCloudsQuality': 'Off',
        'MaxDynamicDecals': 'Low',
        'ScreenSpaceReflectionsQuality': 'Off',
        'SubsurfaceScatteringQuality': 'Low',
        'AmbientOcclusion': 'Off',
        'ColorPrecision': 'Medium',
        'GlobaIlluminationRange': 'High',  # lowest value offered; key is misspelled by the game
        'MirrorQuality': 'Low',
        'LODPreset': 'Low',
    },
    '/graphics/basic': {
        'FilmGrain': False,
        'ChromaticAberration': False,
        'DepthOfField': False,
        'LensFlares': False,
        'MotionBlur': 'Off',
        'Vignette': False,
    },
    '/graphics/performance': {'CrowdDensity': 'Low'},
    '/gameplay/performance': {'CrowdDensity': 'Low'},
    '/audio/misc': {'MuteInBackground': True},
    '/gameplay/hud': {'AutosaveInterval': 'Every 30 minutes'},
}


class SettingsError(Exception):
    """Raised when the settings file does not match the expected 2.31a layout."""


def build_profile(fps, resolution, upscaler):
    profile = {group: dict(options) for group, options in POTATO.items()}
    profile['/video/display']['MaximumFPS_Value'] = fps
    profile['/video/display']['Resolution'] = resolution
    if upscaler == 'dlss_perf':
        profile['/graphics/presets']['ResolutionScaling'] = 'DLSS'
        profile['/graphics/presets']['DLSS'] = 'Performance'
    else:
        profile['/graphics/presets']['ResolutionScaling'] = 'Off'
    return profile


def set_option(group_name, option, new_value):
    option_type = option['type']
    if option_type == 'bool':
        option['value'] = bool(new_value)
    elif option_type == 'int':
        low, high = option.get('min_value'), option.get('max_value')
        if low is not None and not low <= new_value <= high:
            raise SettingsError(f"{group_name}/{option['name']}={new_value} outside [{low},{high}]")
        option['value'] = int(new_value)
    elif option_type in ('string_list', 'name_list', 'int_list'):
        allowed = option.get('values')
        if allowed is not None:
            if new_value not in allowed:
                raise SettingsError(f"{group_name}/{option['name']}={new_value!r} not in {allowed}")
            option['index'] = allowed.index(new_value)
        else:
            known = DYNAMIC_INDEX.get((group_name, option['name']), {})
            if new_value in known:
                option['index'] = known[new_value]
            # Resolution is matched by its value string; its index depends on the monitor mode list.
        option['value'] = new_value
    else:
        raise SettingsError(f"unsupported option type {option_type} for {group_name}/{option['name']}")


def apply_profile(settings, profile):
    groups = {group['group_name']: group for group in settings['data']}
    changes = []
    for group_name, wanted in profile.items():
        group = groups.get(group_name)
        if group is None:
            raise SettingsError(f'group {group_name} missing (game version mismatch?)')
        options = {option['name']: option for option in group['options']}
        for name, new_value in wanted.items():
            option = options.get(name)
            if option is None:
                raise SettingsError(f'option {group_name}/{name} missing (game version mismatch?)')
            old_value = option.get('value')
            set_option(group_name, option, new_value)
            if old_value != option['value']:
                changes.append(f'{group_name}/{name}: {old_value!r} -> {option["value"]!r}')
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source')
    parser.add_argument('dest')
    parser.add_argument('--fps', type=int, default=60)
    parser.add_argument('--res', default='1280x720')
    parser.add_argument('--upscaler', choices=('off', 'dlss_perf'), default='off')
    args = parser.parse_args()

    dest = Path(args.dest).resolve()
    if LIVE_SETTINGS.exists() and dest == LIVE_SETTINGS.resolve():
        sys.exit('refusing to overwrite the live UserSettings.json; write to a copy')

    settings = json.loads(Path(args.source).read_text(encoding='utf-8'))
    if settings.get('version') != 140:
        print(f"warning: settings version {settings.get('version')} (profile written for 140 / game 2.31a)")
    try:
        changes = apply_profile(settings, build_profile(args.fps, args.res, args.upscaler))
    except SettingsError as error:
        sys.exit(f'error: {error}')

    with open(dest, 'w', encoding='utf-8', newline='\r\n') as handle:
        json.dump(settings, handle, indent=4, ensure_ascii=False)
    print(f'{len(changes)} options changed -> {dest}')
    for line in changes:
        print('  ' + line)


if __name__ == '__main__':
    main()

