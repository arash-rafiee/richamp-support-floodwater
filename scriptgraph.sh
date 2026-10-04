#python generateGraphs.py --stations OBS_STATIONS.json --obsExists true --wavesExists true --waveswh /scratch3/workspace/arash_rafiee_uri_edu-richamp/ERIN/forecast_RI_track_clean/swan_HS.63.nc --tempDir /scratch3/workspace/arash_rafiee_uri_edu-richamp/post_temp/ --backgroundChoice RHODE_ISLAND_CHAMP
#python generateGraphs.py --stations OBS_STATIONS.json --meshExists true --mesh /scratch3/workspace/arash_rafiee_uri_edu-richamp/ecflow_output/ricv1_ofcl_veer_Henri_openBarrier/archive/advisory_017/adcirc/forecast/forecast_ofcl/fort.14 --tempDir /scratch3/workspace/arash_rafiee_uri_edu-richamp/post_temp/ --backgroundChoice NORTH_PROVIDENCE
#
#python generateGraphs.py \
#  --stations OBS_STATIONS.json \
#  --meshExists true \
#  --mesh /scratch3/workspace/arash_rafiee_uri_edu-richamp/ecflow_output/ricv1_ofcl_veer_Henri_openBarrier/archive/advisory_017/adcirc/forecast/forecast_ofcl/fort.14 \
#  --tempDir /scratch3/workspace/arash_rafiee_uri_edu-richamp/post_temp/ \
#  --backgroundChoice NORTH_PROVIDENCE
#
#
#python generateGraphs.py \
#  --stations OBS_STATIONS.json \
#  --obsExists true \
#  --adcircExists true \
#  --wind /work/pi_reza_hashemi_uri_edu/01_RI_CHAMP_Model/01_RICHAMP_Backup/ricv1_ofcl_veer_Henri/archive/advisory_017/adcirc/forecast/forecast_ofcl/fort.74.nc \
#  --tempDir /scratch3/workspace/arash_rafiee_uri_edu-richamp/post_temp/ \
#  --backgroundChoice RHODE_ISLAND_CHAMP
#
#
#python generateGraphs.py \
#  --stations OBS_STATIONS.json \
#  --wavesExists true \
#  --waveswh /scratch3/workspace/arash_rafiee_uri_edu-richamp/ERIN_NEW/forecast_RI_track_clean/swan_HS.63.nc \
#  --wavemwd /scratch3/workspace/arash_rafiee_uri_edu-richamp/ERIN_NEW/forecast_RI_track_clean/swan_DIR.63.nc \
#  --wavemwp /scratch3/workspace/arash_rafiee_uri_edu-richamp/ERIN_NEW/forecast_RI_track_clean/swan_TMM10.63.nc \
#  --wavepwp /scratch3/workspace/arash_rafiee_uri_edu-richamp/ERIN_NEW/forecast_RI_track_clean/swan_TPS.63.nc \
#  --waverad /scratch3/workspace/arash_rafiee_uri_edu-richamp/ERIN_NEW/forecast_RI_track_clean/rads.64.nc \
#  --tempDir /scratch3/workspace/arash_rafiee_uri_edu-richamp/post_temp/ \
#  --backgroundChoice NARRAGANSETT_MOUTH
#
#
#python generateGraphs.py \
#  --stations OBS_STATIONS.json \
#  --wavesExists true \
#  --waveswh /scratch3/workspace/arash_rafiee_uri_edu-richamp/ERIN/forecast_RI_track_clean/swan_HS.63.nc \
#  --tempDir /scratch3/workspace/arash_rafiee_uri_edu-richamp/post_temp/ \
#  --backgroundChoice RHODE_ISLAND_CHAMP
#
#
#python generateGraphs.py \
#  --stations OBS_STATIONS.json \
#  --obsExists true \
#  --velocityExists true \
#  --velocity /scratch4/workspace/arash_rafiee_uri_edu-richamp/ecflow_output/ricv1/archive/20260127/hour_12/adcirc/analysis/fort.64.nc \
#  --tempDir /scratch4/workspace/arash_rafiee_uri_edu-richamp/post_temp/ \
#  --backgroundChoice RHODE_ISLAND_CHAMP
#
#
# The banner at the top of each graph comes from run.properties by default.
# To show the first and last time of the input .nc file instead, add:
#   --bannerFromData true \
# To name the wind forcing in the banner (e.g. GDAS instead of run.properties' gfs), add:
#   --windProduct GDAS \
# Graph units are metric (m, m/s, mm) by default; for feet, mph and inches, add:
#   --unit imperial \
# To plot NOAA tide predictions instead of the observed water levels, add:
#   --obsPredicted true \
# To also draw map frames over --backgroundChoice and save them as an .avi video
# (water is .mp4 instead when an ffmpeg program is already on the PATH), add:
#   --maps true \
#
python generateGraphs.py \
  --stations OBS_STATIONS.json \
  --obsExists true \
  --waterExists true \
  --water /scratch4/workspace/arash_rafiee_uri_edu-richamp/ecflow_output/ricv1/archive/20260127/hour_12/adcirc/analysis/fort.63.nc \
  --tempDir /scratch4/workspace/arash_rafiee_uri_edu-richamp/post_temp/ \
  --backgroundChoice RHODE_ISLAND_CHAMP
